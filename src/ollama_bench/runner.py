from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .machine import discover_machine, unknown_machine
from .models import comparable_tag
from .ollama import OllamaClient, OllamaError


RESULT_FIELDS = [
    "run_id", "attempt", "task_id", "suite_version", "suite_sha256", "status", "model_tag", "quantization",
    "ollama_version", "started_at_utc", "finished_at_utc", "generation_seconds",
    "public_test_seconds", "final_test_seconds", "repairs", "api_calls", "api_retries",
    "metadata_api_calls", "metadata_api_retries", "prompt_tokens", "completion_tokens",
    "ollama_total_seconds", "load_seconds", "prompt_eval_seconds", "eval_seconds",
    "public_status", "final_status", "notes",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "item"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    code = max(blocks, key=len).strip() if blocks else text.strip()
    if not code:
        return ""
    return code + "\n"


def _model_quantization(details: dict | None, show: dict | None) -> str:
    value = ((details or {}).get("details") or {}).get("quantization_level")
    if not value:
        value = ((show or {}).get("details") or {}).get("quantization_level")
    if not value:
        model_info = (show or {}).get("model_info") or {}
        value = next((v for k, v in model_info.items() if k.endswith("quantization_version")), None)
    return str(value) if value else "unknown"


def _metric_seconds(response: dict, key: str) -> str:
    value = response.get(key)
    if isinstance(value, (int, float)) and value >= 0:
        return f"{value / 1_000_000_000:.6f}"
    return "unknown"


def _accumulate_metric(record: dict[str, Any], field: str, response: dict, source: str, scale: float = 1.0) -> None:
    value = response.get(source)
    if isinstance(value, (int, float)) and value >= 0:
        previous = record.get(field, 0)
        record[field] = (previous if isinstance(previous, (int, float)) else 0) + value / scale


class ResourceSampler:
    """Periodically append sanitized Windows observations while a call is active."""

    def __init__(self, script: Path, output: Path, interval: float, min_ram_gib: float = 0, min_disk_gib: float = 0, disk_paths: tuple[Path, ...] = ()):
        self.script = script
        self.output = output
        self.interval = interval
        self.min_ram_gib = min_ram_gib
        self.min_disk_gib = min_disk_gib
        self.disk_paths = disk_paths
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.breach_message = ""

    def __enter__(self):
        self.stop_event = threading.Event()
        self.breach_message = ""
        self.thread = threading.Thread(target=self._sample, name="ollama-bench-resource-sampler", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=35)

    def _sample(self):
        while not self.stop_event.wait(self.interval):
            try:
                value = discover_machine(self.script, timeout=min(30, max(5, int(self.interval))))
            except Exception as exc:
                value = unknown_machine(str(exc))
            value["observation_kind"] = "periodic resource sample"
            available = (value.get("memory") or {}).get("available_mib")
            if isinstance(available, (int, float)) and available < self.min_ram_gib * 1024:
                self.breach_message = f"available RAM dropped below the configured {self.min_ram_gib:.1f} GiB floor"
            for path in self.disk_paths:
                try:
                    free = shutil.disk_usage(path).free / (1024 ** 3)
                    if free < self.min_disk_gib:
                        self.breach_message = f"free disk space dropped below the configured {self.min_disk_gib:.1f} GiB floor"
                except OSError:
                    continue
            self.output.parent.mkdir(parents=True, exist_ok=True)
            with self.output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def _run_pytest(test_file: Path, candidate: Path, timeout: int, root: Path, artifact: Path) -> tuple[str, float, str]:
    env = os.environ.copy()
    env["BENCH_CANDIDATE"] = str(candidate.resolve())
    env["BENCH_PROJECT_ROOT"] = str(root.resolve())
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)]
    started = time.monotonic()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags)
        try:
            output, _ = proc.communicate(timeout=timeout)
            status = "pass" if proc.returncode == 0 else "fail"
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
            status = "incomplete"
            output += f"\nRunner stopped the test process after its {timeout}s time limit.\n"
        except KeyboardInterrupt:
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                output, _ = proc.communicate(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                output, _ = proc.communicate()
            artifact.write_text(output + "\nOperator interrupted the test process.\n", encoding="utf-8", errors="replace")
            raise
    except OSError as exc:
        output = f"Could not start pytest: {exc}\n"
        status = "incomplete"
    elapsed = time.monotonic() - started
    artifact.write_text(output, encoding="utf-8", errors="replace")
    return status, elapsed, output


def _summary(results_path: Path, summary_path: Path) -> None:
    latest: dict[tuple[str, str], dict[str, str]] = {}
    if results_path.exists():
        with results_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                latest[(row["task_id"], row["model_tag"])] = row
    with summary_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(latest.values())


def _append_result(path: Path, row: dict[str, Any], summary_path: Path) -> None:
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in RESULT_FIELDS})
    _summary(path, summary_path)


def _resource_gate(machine: dict, output_disk: Path, config: dict, ask: Callable[[str], str]) -> tuple[bool, str]:
    problems: list[str] = []
    available = ((machine.get("memory") or {}).get("available_mib"))
    if isinstance(available, (int, float)):
        if available < config["min_available_ram_gib"] * 1024:
            problems.append(f"available RAM is {available / 1024:.1f} GiB, below the configured {config['min_available_ram_gib']:.1f} GiB floor")
    else:
        problems.append("available RAM is unknown")
    try:
        model_store = Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama"))).expanduser()
        output_free = shutil.disk_usage(output_disk).free / (1024 ** 3)
        model_free = shutil.disk_usage(model_store if model_store.exists() else model_store.parent).free / (1024 ** 3)
        free = min(output_free, model_free)
        if free < config["min_free_disk_gib"]:
            problems.append(f"free disk space in the benchmark or Ollama model storage volume is {free:.1f} GiB, below the configured {config['min_free_disk_gib']:.1f} GiB floor")
    except OSError:
        problems.append("free disk space is unknown")
    if not problems:
        return True, "resource preflight passed"
    note = "; ".join(problems)
    return ask(f"Resource preflight: {note}. Continue this model? Type YES to proceed: ").strip() == "YES", note


def _execution_gate(allow_host_execution: bool, ask: Callable[[str], str]) -> bool:
    if allow_host_execution:
        return True
    warning = (
        "The generated candidate will execute as a subprocess under your current Windows account. "
        "This project does not sandbox generated code; a Python venv, pytest process and socket mocks are not a security boundary.\n"
        "Review the candidate first, then type HOST to permit its public and final tests to run: "
    )
    return ask(warning).strip() == "HOST"


def _prompt(task_dir: Path, phase: str, previous_candidate: str = "", feedback: str = "") -> str:
    spec = (task_dir / "SPEC.md").read_text(encoding="utf-8")
    public_source = (task_dir / "test_public.py").read_text(encoding="utf-8")
    if phase == "initial":
        return (
            "Implement candidate.py to satisfy this versioned coding task. Return complete Python source only. "
            "Do not use network access outside the task's specified local/mock transport.\n\n"
            f"TASK SPECIFICATION:\n{spec}\n\nPUBLIC TESTS (feedback is available to all models):\n{public_source}"
        )
    return (
        "Repair candidate.py using only this public-test output. Return the complete replacement Python source only.\n\n"
        f"TASK SPECIFICATION:\n{spec}\n\nCURRENT CANDIDATE:\n```python\n{previous_candidate}\n```\n\n"
        f"PUBLIC TEST OUTPUT:\n{feedback}"
    )


def _task_version(task_dir: Path) -> str:
    try:
        metadata = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        return str(metadata.get("suite_version", "unknown"))
    except (OSError, json.JSONDecodeError):
        return "unknown"


def _task_hash(task_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("task.json", "SPEC.md", "test_public.py", "test_final.py"):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((task_dir / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _new_run(root: Path, config: dict, tags: list[str], client: OllamaClient) -> tuple[str, Path, dict]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{os.urandom(3).hex()}"
    run_dir = root / config["output_dir"] / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    models, retries = client.list_models()
    try:
        version, version_retries = client.version()
    except OllamaError as exc:
        version, version_retries = {"version": "unknown", "error": str(exc)}, 0
    write_json(run_dir / "installed_models.json", {"models": models, "api_retries": retries})
    try:
        machine = discover_machine(root / "scripts" / "Discover-Machine.ps1")
    except Exception as exc:
        machine = unknown_machine(str(exc))
    write_json(run_dir / "machine.json", machine)
    write_json(run_dir / "run_config.json", {"config": config, "model_tags": tags, "created_at_utc": utc_now(), "installed_models": models, "ollama_version": version})
    (run_dir / "resource_observations.jsonl").write_text(json.dumps(machine, ensure_ascii=False) + "\n", encoding="utf-8")
    return run_id, run_dir, {"installed": models, "machine": machine, "list_retries": retries, "ollama_version": version, "version_retries": version_retries}


def _resume_run(root: Path, config: dict, run_id: str) -> tuple[Path, dict, dict]:
    run_dir = root / config["output_dir"] / safe_name(run_id)
    if not (run_dir / "run_config.json").is_file():
        raise ValueError(f"Unknown run ID: {run_id}")
    state = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    config = state["config"]
    machine = json.loads((run_dir / "machine.json").read_text(encoding="utf-8"))
    resume_client = OllamaClient(config["ollama_url"], config["model_call_timeout_seconds"], retries=0)
    installed, retries = resume_client.list_models()
    try:
        version, version_retries = resume_client.version()
    except OllamaError as exc:
        version, version_retries = {"version": "unknown", "error": str(exc)}, 0
    return run_dir, {"installed": installed, "machine": machine, "list_retries": retries, "ollama_version": version, "version_retries": version_retries}, state


def _validate_output_dir(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("output_dir must stay inside this project")
    resolved_root = root.resolve()
    output = (resolved_root / path).resolve()
    if output != resolved_root and resolved_root not in output.parents:
        raise ValueError("output_dir must stay inside this project")
    return output


def _validate_task_names(root: Path, task_names: list[str]) -> None:
    suites = (root / "tasks").resolve()
    for name in task_names:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError(f"Invalid task suite name: {name!r}")
        path = (suites / name).resolve()
        if path.parent != suites or not path.is_dir():
            raise ValueError(f"Task suite not found inside tasks/: {name}")


def _markdown_report(run_dir: Path) -> None:
    csv_path = run_dir / "summary.csv"
    if not csv_path.exists():
        return
    with csv_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    lines = [
        "# Local Ollama benchmark report", "", f"Run ID: `{run_dir.name}`", "",
        "Results describe only these task versions, settings, host observations and run artifacts. They are not a general model-quality ranking.", "",
        "Generated candidates ran on the host only after the operator typed `HOST` or passed `--allow-host-execution`. No OS sandbox is provided.", "",
        "| Task | Suite | Model | Quantization | Outcome | Public checks | Final checks | Generation | Public time | Final time | Repairs | Calls/retries |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append("| {task_id} | {suite_version} | {model_tag} | {quantization} | {status} | {public_status} | {final_status} | {generation_seconds}s | {public_test_seconds}s | {final_test_seconds}s | {repairs} | {api_calls}/{api_retries} |".format(**row))
    config_path = run_dir / "run_config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8")).get("config", {})
        report_settings = {key: config.get(key, "unknown") for key in (
            "temperature", "seed", "num_ctx", "num_predict", "think", "keep_alive", "max_repairs",
            "model_call_timeout_seconds", "public_test_timeout_seconds", "final_test_timeout_seconds",
            "min_available_ram_gib", "min_free_disk_gib",
        )}
        lines += ["", "## Runtime settings", "", "`generation_seconds` is wall time around each local chat request; Ollama-reported durations are separate. Test durations exclude generation. Model download time is stored in its download record and is excluded from generation time.", "", "```json", json.dumps(report_settings, indent=2), "```", ""]
    machine_path = run_dir / "machine.json"
    if machine_path.exists():
        machine = json.loads(machine_path.read_text(encoding="utf-8"))
        lines += ["", "## Host observations", "", "Measurements marked `unknown` were not available. Machine identifiers are intentionally omitted.", "", "```json", json.dumps({"os": machine.get("os"), "cpu": machine.get("cpu"), "memory": machine.get("memory"), "disks": machine.get("disks"), "gpus": machine.get("gpus"), "monitoring_capabilities": machine.get("monitoring_capabilities")}, indent=2), "```", ""]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(root: Path, config: dict, tags: list[str], resume: str | None = None, allow_host_execution: bool = False,
                  ask: Callable[[str], str] = input, client_factory: Callable[..., OllamaClient] = OllamaClient) -> Path:
    _validate_output_dir(root, config["output_dir"])
    _validate_task_names(root, config["tasks"])
    client = client_factory(config["ollama_url"], config["model_call_timeout_seconds"], retries=1)
    if resume:
        run_dir, startup, run_state = _resume_run(root, config, resume)
        run_id = run_dir.name
        tags = run_state["model_tags"]
        config = run_state["config"]
    else:
        run_id, run_dir, startup = _new_run(root, config, tags, client)
    installed: dict[str, dict] = {}
    for item in startup["installed"].get("models", []):
        actual_name = item.get("name") or item.get("model")
        if actual_name:
            installed[actual_name] = item
            installed[comparable_tag(actual_name)] = item
    task_names = config["tasks"]
    prior: dict[tuple[str, str], dict] = {}
    result_path = run_dir / "results.csv"
    if result_path.exists():
        with result_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                prior[(row["task_id"], row["model_tag"])] = row
    machine = startup["machine"]
    out_root = _validate_output_dir(root, config["output_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    model_store = Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama"))).expanduser()
    sampler = ResourceSampler(
        root / "scripts" / "Discover-Machine.ps1", run_dir / "resource_observations.jsonl",
        config["resource_sample_seconds"], config["min_available_ram_gib"], config["min_free_disk_gib"],
        (out_root, model_store if model_store.exists() else model_store.parent),
    )

    try:
        for tag in tags:
            model_info = installed.get(tag) or installed.get(comparable_tag(tag))
            if model_info is None:
                if ask(f"Model {tag} is not installed. Download it using local Ollama now? Type YES to download: ").strip() != "YES":
                    for task_name in task_names:
                        if prior.get((task_name, tag), {}).get("status") in {"pass", "fail", "resource_stop"}:
                            continue
                        task_dir = root / "tasks" / task_name
                        _append_result(result_path, {"run_id": run_id, "attempt": 1, "task_id": task_name, "suite_version": _task_version(task_dir), "suite_sha256": _task_hash(task_dir), "status": "skipped", "model_tag": tag, "started_at_utc": utc_now(), "finished_at_utc": utc_now(), "notes": "Model download declined; model was not installed."}, run_dir / "summary.csv")
                    continue
                allowed, gate_note = _resource_gate(machine, out_root, config, ask)
                if not allowed:
                    for task_name in task_names:
                        if prior.get((task_name, tag), {}).get("status") in {"pass", "fail", "resource_stop"}:
                            continue
                        resource_row = {"run_id": run_id, "attempt": 1, "task_id": task_name, "suite_version": _task_version(root / "tasks" / task_name), "suite_sha256": _task_hash(root / "tasks" / task_name), "status": "resource_stop", "model_tag": tag, "started_at_utc": utc_now(), "finished_at_utc": utc_now(), "notes": gate_note}
                        _append_result(result_path, resource_row, run_dir / "summary.csv")
                    continue
                try:
                    pull_started = utc_now()
                    pull_clock = time.monotonic()
                    with sampler:
                        pull, pull_retries = client.pull_model(tag)
                except OllamaError as exc:
                    for task_name in task_names:
                        task_dir = root / "tasks" / task_name
                        record = {"run_id": run_id, "attempt": 1, "task_id": task_name, "suite_version": _task_version(task_dir), "suite_sha256": _task_hash(task_dir), "status": "incomplete", "model_tag": tag, "started_at_utc": utc_now(), "finished_at_utc": utc_now(), "notes": f"Model download failed: {exc}"}
                        _append_result(result_path, record, run_dir / "summary.csv")
                    continue
                write_json(run_dir / "downloads" / f"{safe_name(tag)}.json", {"model_tag": tag, "started_at_utc": pull_started, "completed_at_utc": utc_now(), "elapsed_seconds": time.monotonic() - pull_clock, "result": pull, "retries": pull_retries})
                if sampler.breach_message:
                    for task_name in task_names:
                        task_dir = root / "tasks" / task_name
                        stop_row = {"run_id": run_id, "attempt": 1, "task_id": task_name, "suite_version": _task_version(task_dir), "suite_sha256": _task_hash(task_dir), "status": "resource_stop", "model_tag": tag, "started_at_utc": pull_started, "finished_at_utc": utc_now(), "notes": sampler.breach_message}
                        _append_result(result_path, stop_row, run_dir / "summary.csv")
                    continue
                model_info = {"name": tag}
            for task_name in task_names:
                old = prior.get((task_name, tag))
                terminal = {"pass", "fail"}
                if not resume:
                    terminal.update({"skipped", "resource_stop"})
                if old and old.get("status") in terminal:
                    continue
                task_dir = root / "tasks" / task_name
                if task_dir.resolve().parent != (root / "tasks").resolve():
                    raise ValueError(f"Task suite path escapes the tasks directory: {task_name}")
                if not (task_dir / "SPEC.md").is_file() or not (task_dir / "test_public.py").is_file() or not (task_dir / "test_final.py").is_file():
                    raise ValueError(f"Task suite is incomplete: {task_name}")
                started_at = utc_now()
                try:
                    version_info = startup.get("ollama_version") or {}
                    ollama_version = version_info.get("version", "unknown")
                except AttributeError:
                    ollama_version = "unknown"
                record: dict[str, Any] = {
                    "run_id": run_id, "attempt": (int(old.get("attempt", 0)) + 1 if old else 1),
                    "task_id": task_name, "suite_version": _task_version(task_dir), "suite_sha256": _task_hash(task_dir), "model_tag": tag,
                    "ollama_version": ollama_version,
                    "quantization": _model_quantization(model_info, None), "started_at_utc": started_at,
                    "api_calls": 0, "api_retries": 0, "metadata_api_calls": 0, "metadata_api_retries": 0,
                    "prompt_tokens": 0, "completion_tokens": 0, "ollama_total_seconds": 0,
                    "load_seconds": 0, "prompt_eval_seconds": 0, "eval_seconds": 0,
                    "repairs": 0, "generation_seconds": 0,
                    "public_test_seconds": 0, "final_test_seconds": 0,
                }
                task_run = run_dir / "models" / safe_name(tag) / safe_name(task_name) / f"attempt-{record['attempt']:02d}"
                task_run.mkdir(parents=True, exist_ok=False)
                try:
                    show, retries = client.show_model(tag)
                    record["metadata_api_calls"] = 1 + retries
                    record["metadata_api_retries"] = retries
                    record["quantization"] = _model_quantization(model_info, show)
                    listed_safe = {key: model_info.get(key) for key in ("name", "model", "size", "digest", "modified_at", "details") if key in model_info}
                    show_safe = {key: show.get(key) for key in ("details", "model_info", "capabilities") if key in show}
                    write_json(task_run / "model_metadata.json", {"tag": tag, "listed": listed_safe, "show": show_safe})
                except OllamaError as exc:
                    record["notes"] = f"Model metadata unavailable: {exc}"

                allowed, gate_note = _resource_gate(machine, out_root, config, ask)
                if not allowed:
                    record.update(status="resource_stop", notes=gate_note, finished_at_utc=utc_now())
                    _append_result(result_path, record, run_dir / "summary.csv")
                    prior[(task_name, tag)] = record
                    continue
                response: dict = {}
                response_text = ""
                code = ""
                candidate_path: Path | None = None
                try:
                    for repair in range(config["max_repairs"] + 1):
                        prompt = _prompt(task_dir, "initial" if repair == 0 else "repair", code, record.get("last_public_output", ""))
                        label = "initial" if repair == 0 else f"repair-{repair}"
                        (task_run / f"prompt-{label}.txt").write_text(prompt, encoding="utf-8")
                        generation_start = time.monotonic()
                        with sampler:
                            response, retries = client.chat(tag, prompt, config)
                        record["generation_seconds"] += time.monotonic() - generation_start
                        record["api_calls"] += 1
                        record["api_retries"] += retries
                        _accumulate_metric(record, "prompt_tokens", response, "prompt_eval_count")
                        _accumulate_metric(record, "completion_tokens", response, "eval_count")
                        _accumulate_metric(record, "ollama_total_seconds", response, "total_duration", 1_000_000_000)
                        _accumulate_metric(record, "load_seconds", response, "load_duration", 1_000_000_000)
                        _accumulate_metric(record, "prompt_eval_seconds", response, "prompt_eval_duration", 1_000_000_000)
                        _accumulate_metric(record, "eval_seconds", response, "eval_duration", 1_000_000_000)
                        response_text = ((response.get("message") or {}).get("content") or "")
                        raw = json.dumps(response, indent=2, ensure_ascii=False, default=str) + "\n"
                        (task_run / f"response-{label}.json").write_text(raw, encoding="utf-8")
                        code = extract_code(response_text)
                        candidate_path = task_run / f"candidate-{label}.py"
                        candidate_path.write_text(code, encoding="utf-8")
                        if sampler.breach_message:
                            record.update(status="resource_stop", notes=sampler.breach_message)
                            break
                        if not code.strip():
                            record.update(status="incomplete", notes="Ollama returned an empty candidate.")
                            break
                        if repair == 0 and not _execution_gate(allow_host_execution or config["allow_host_execution"], ask):
                            record.update(status="skipped", notes="Host execution was declined; generated source remains saved for review.")
                            break
                        with sampler:
                            public_status, elapsed, test_output = _run_pytest(task_dir / "test_public.py", candidate_path, int(config["public_test_timeout_seconds"]), root, task_run / f"public-tests-{label}.txt")
                        record["public_test_seconds"] += elapsed
                        record["last_public_output"] = test_output[-12000:]
                        if public_status == "pass":
                            record["public_status"] = "pass"
                            break
                        record["public_status"] = public_status
                        if sampler.breach_message:
                            record.update(status="resource_stop", notes=sampler.breach_message)
                            break
                        if public_status == "incomplete" or repair >= config["max_repairs"]:
                            break
                        record["repairs"] += 1
                    if record.get("status") in {"incomplete", "skipped", "resource_stop"}:
                        pass
                    elif record.get("public_status") == "incomplete":
                        record.update(status="incomplete", notes="Public tests reached their configured time limit.")
                    else:
                        if candidate_path is None:
                            record.update(status="incomplete", notes="No candidate was saved.")
                            continue
                        with sampler:
                            final_status, elapsed, final_output = _run_pytest(task_dir / "test_final.py", candidate_path, int(config["final_test_timeout_seconds"]), root, task_run / "final-tests.txt")
                        record["final_test_seconds"] += elapsed
                        record["final_status"] = final_status
                        if sampler.breach_message:
                            record.update(status="resource_stop", notes=sampler.breach_message)
                        elif final_status == "incomplete" or record.get("public_status") == "incomplete":
                            record.update(status="incomplete", notes="A test suite reached its configured time limit.")
                        elif final_status == "pass" and record.get("public_status") == "pass":
                            record["status"] = "pass"
                        else:
                            record.update(status="fail", notes="The final candidate did not pass both public and final suites.")
                except KeyboardInterrupt:
                    record.update(status="interrupted", notes="Interrupted by operator; completed files and prior results were preserved.")
                    raise
                except (OllamaError, OSError, ValueError) as exc:
                    record.update(status="incomplete", notes=str(exc))
                finally:
                    record["finished_at_utc"] = utc_now()
                    for metric_name in ("prompt_tokens", "completion_tokens", "ollama_total_seconds", "load_seconds", "prompt_eval_seconds", "eval_seconds"):
                        if record.get(metric_name) == 0:
                            record[metric_name] = "unknown"
                    record.pop("last_public_output", None)
                    _append_result(result_path, record, run_dir / "summary.csv")
                    prior[(task_name, tag)] = record
                    _markdown_report(run_dir)
    except KeyboardInterrupt:
        _markdown_report(run_dir)
    _markdown_report(run_dir)
    return run_dir
