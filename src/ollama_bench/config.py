from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path("config/benchmark.json")


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "ollama_url", "models_csv", "tasks", "output_dir",
        "resource_sample_seconds", "public_test_timeout_seconds",
        "final_test_timeout_seconds", "model_call_timeout_seconds",
        "max_repairs", "min_available_ram_gib", "min_free_disk_gib",
        "temperature", "seed", "num_ctx", "num_predict", "keep_alive",
        "think", "allow_host_execution",
    }
    missing = required - config.keys()
    if missing:
        raise ValueError(f"Configuration is missing: {', '.join(sorted(missing))}")
    if not isinstance(config["max_repairs"], int) or isinstance(config["max_repairs"], bool) or not 0 <= config["max_repairs"] <= 10:
        raise ValueError("max_repairs must be an integer from 0 through 10")
    for key in ("resource_sample_seconds", "public_test_timeout_seconds", "final_test_timeout_seconds", "model_call_timeout_seconds", "num_ctx", "num_predict"):
        if not isinstance(config[key], (int, float)) or isinstance(config[key], bool) or config[key] <= 0:
            raise ValueError(f"{key} must be positive")
    for key in ("min_available_ram_gib", "min_free_disk_gib"):
        if not isinstance(config[key], (int, float)) or isinstance(config[key], bool) or config[key] < 0:
            raise ValueError(f"{key} must be nonnegative")
    for key in ("temperature", "seed"):
        if not isinstance(config[key], (int, float)) or isinstance(config[key], bool):
            raise ValueError(f"{key} must be numeric")
    if not isinstance(config["tasks"], list) or not config["tasks"]:
        raise ValueError("tasks must be a non-empty list")
    if not all(isinstance(name, str) and name for name in config["tasks"]):
        raise ValueError("every task entry must be a non-empty suite name")
    if not isinstance(config["allow_host_execution"], bool) or not isinstance(config["think"], bool):
        raise ValueError("allow_host_execution and think must be true or false")
    if not isinstance(config["keep_alive"], str) or not config["keep_alive"]:
        raise ValueError("keep_alive must be a non-empty Ollama duration string")
    return config
