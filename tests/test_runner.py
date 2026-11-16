import csv
import json
import shutil
from pathlib import Path

from ollama_bench.runner import run_benchmark


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = (ROOT / "tests" / "fixtures" / "reference_candidate.py").read_text(encoding="utf-8")


class FakeOllama:
    def __init__(self, *_args, **_kwargs):
        self.calls = 0
    def list_models(self):
        return {"models": [{"name": "fixture:1", "details": {"quantization_level": "Q4_K_M"}}]}, 0
    def version(self):
        return {"version": "fixture"}, 0
    def show_model(self, _tag):
        return {"details": {"quantization_level": "Q4_K_M"}}, 0
    def pull_model(self, _tag):
        raise AssertionError("fixture model is already installed")
    def chat(self, _tag, _prompt, _settings):
        self.calls += 1
        return {"message": {"content": REFERENCE}, "prompt_eval_count": 100, "eval_count": 200, "total_duration": 1_000_000_000}, 0


def setup_root(tmp_path):
    for task in ("api-sync-v1", "api-sync-v2"):
        shutil.copytree(ROOT / "tasks" / task, tmp_path / "tasks" / task)
    (tmp_path / "scripts").mkdir()
    return tmp_path


def settings(task, output="runs"):
    return {
        "ollama_url": "http://127.0.0.1:11434", "models_csv": "models.csv", "tasks": [task],
        "output_dir": output, "resource_sample_seconds": 10000,
        "public_test_timeout_seconds": 60, "final_test_timeout_seconds": 60,
        "model_call_timeout_seconds": 30, "max_repairs": 0,
        "min_available_ram_gib": 0, "min_free_disk_gib": 0,
        "temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 4096,
        "keep_alive": "5m", "think": False, "allow_host_execution": False,
    }


def test_runner_executes_versioned_suite_and_saves_evidence(tmp_path, monkeypatch):
    from ollama_bench import runner
    project = setup_root(tmp_path)
    monkeypatch.setattr(runner, "discover_machine", lambda *_args, **_kwargs: {
        "os": {"windows_11_supported": True}, "memory": {"available_mib": 32000},
        "cpu": {"name": "fixture"}, "disks": [], "gpus": [], "monitoring_capabilities": {},
    })
    run_dir = run_benchmark(project, settings("api-sync-v2"), ["fixture:1"], allow_host_execution=True, client_factory=FakeOllama)
    with (run_dir / "summary.csv").open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["status"] == "pass"
    assert row["suite_version"] == "2.0.0"
    assert len(row["suite_sha256"]) == 64
    assert row["api_calls"] == "1"
    assert row["quantization"] == "Q4_K_M"
    assert row["public_status"] == "pass"
    assert row["final_status"] == "pass"
    task_run = run_dir / "models" / "fixture_1" / "api-sync-v2" / "attempt-01"
    assert (task_run / "prompt-initial.txt").is_file()
    assert (task_run / "response-initial.json").is_file()
    assert (task_run / "candidate-initial.py").is_file()
    assert (task_run / "public-tests-initial.txt").is_file()
    assert (task_run / "final-tests.txt").is_file()
    assert "not a general model-quality ranking" in (run_dir / "report.md").read_text(encoding="utf-8")
    run_config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert run_config["config"]["seed"] == 42


def test_runner_persists_download_decline_as_skipped(tmp_path, monkeypatch):
    from ollama_bench import runner
    project = setup_root(tmp_path)
    monkeypatch.setattr(runner, "discover_machine", lambda *_args, **_kwargs: {
        "os": {"windows_11_supported": True}, "memory": {"available_mib": 32000},
        "cpu": {}, "disks": [], "gpus": [], "monitoring_capabilities": {},
    })
    class MissingModel(FakeOllama):
        def list_models(self): return {"models": []}, 0
    run_dir = run_benchmark(project, settings("api-sync-v1"), ["missing:1"], ask=lambda _prompt: "NO", client_factory=MissingModel)
    with (run_dir / "summary.csv").open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["status"] == "skipped"
    assert "declined" in row["notes"]
