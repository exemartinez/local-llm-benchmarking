from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_CONFIG, load_config
from .machine import discover_machine
from .models import read_model_tags
from .ollama import OllamaClient, OllamaError
from .runner import run_benchmark


def _project_root(value: str | None) -> Path:
    return Path(value).resolve() if value else Path.cwd().resolve()


def _require_windows_11(machine: dict) -> None:
    if sys.platform != "win32":
        raise RuntimeError("This first version supports Windows 11 only.")
    if machine.get("os", {}).get("windows_11_supported") is False:
        raise RuntimeError("Machine discovery did not identify a supported Windows 11 build.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ollama-bench", description="Run evidence-first local Ollama coding benchmarks on Windows 11.")
    parser.add_argument("--version", action="version", version=f"ollama-bench {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover-machine", help="Print a sanitized machine inventory using PowerShell.")
    discover.add_argument("--root", help="Project directory; defaults to the current directory.")
    doctor = sub.add_parser("doctor", help="Check Windows 11, project configuration and local Ollama connectivity.")
    doctor.add_argument("--root", help="Project directory; defaults to the current directory.")
    doctor.add_argument("--config", default=str(DEFAULT_CONFIG))
    run = sub.add_parser("run", help="Run the configured models and task suites sequentially.")
    run.add_argument("--root", help="Project directory; defaults to the current directory.")
    run.add_argument("--config", default=str(DEFAULT_CONFIG))
    run.add_argument("--resume", help="Resume an interrupted run by its run ID.")
    run.add_argument("--allow-host-execution", action="store_true", help="Accept running generated candidates as your current Windows user. No sandbox is provided.")
    args = parser.parse_args(argv)
    root = _project_root(getattr(args, "root", None))
    try:
        if args.command == "discover-machine":
            snapshot = discover_machine(root / "scripts" / "Discover-Machine.ps1")
            print(json.dumps(snapshot, indent=2, ensure_ascii=False))
            return 0
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = root / config_path
        config = load_config(config_path)
        machine = discover_machine(root / "scripts" / "Discover-Machine.ps1")
        _require_windows_11(machine)
        if args.command == "doctor":
            tags = read_model_tags(root / config["models_csv"])
            version, _ = OllamaClient(config["ollama_url"], timeout=10).version()
            print(json.dumps({"project_version": __version__, "windows_11": machine["os"], "ollama": version, "model_tags": tags, "configured_tasks": config["tasks"]}, indent=2))
            return 0
        tags = read_model_tags(root / config["models_csv"])
        run_dir = run_benchmark(root, config, tags, resume=args.resume, allow_host_execution=args.allow_host_execution)
        print(f"Run saved: {run_dir.relative_to(root)}")
        print(f"Summary: {run_dir.relative_to(root) / 'summary.csv'}")
        print(f"Report: {run_dir.relative_to(root) / 'report.md'}")
        return 0
    except (OSError, ValueError, RuntimeError, OllamaError, json.JSONDecodeError) as exc:
        print(f"ollama-bench: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
