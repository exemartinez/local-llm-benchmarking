import json
from pathlib import Path

import pytest

from ollama_bench.config import load_config
from ollama_bench.models import read_model_tags
from ollama_bench.ollama import OllamaClient
from ollama_bench.runner import _task_hash, extract_code


ROOT = Path(__file__).resolve().parents[1]


def test_sample_configuration_is_bounded_and_local():
    config = load_config(ROOT / "config" / "benchmark.json")
    assert config["max_repairs"] == 2
    assert config["allow_host_execution"] is False
    assert config["ollama_url"].startswith("http://127.0.0.1:")


def test_model_csv_loads_ordered_tags():
    assert read_model_tags(ROOT / "models.csv") == ["qwen3.5:4b", "qwen3-coder:30b"]


def test_cloud_model_tags_are_rejected(tmp_path):
    path = tmp_path / "models.csv"
    path.write_text("tag\nexample:cloud\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Cloud model"):
        read_model_tags(path)


def test_cloud_suffix_tags_are_rejected(tmp_path):
    path = tmp_path / "models.csv"
    path.write_text("tag\nexample:20b-cloud\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Cloud model"):
        read_model_tags(path)


@pytest.mark.parametrize("url", ["https://example.com", "http://192.0.2.1:11434", "http://example.invalid"])
def test_ollama_client_rejects_non_loopback(url):
    with pytest.raises(ValueError, match="loopback"):
        OllamaClient(url)


def test_extract_code_keeps_largest_python_block():
    answer = "Explanation\n```python\nx = 1\n```\n```python\ny = 2\nz = 3\n```"
    assert extract_code(answer) == "y = 2\nz = 3\n"


def test_suite_fingerprint_covers_versioned_contract():
    first = _task_hash(ROOT / "tasks" / "api-sync-v2")
    assert len(first) == 64
    assert first != _task_hash(ROOT / "tasks" / "api-sync-v1")


def test_sample_report_is_explicitly_synthetic():
    sample = (ROOT / "examples" / "sample-report.md").read_text(encoding="utf-8")
    assert "synthetic" in sample.lower()


def test_sample_machine_contains_no_identifying_keys():
    machine = json.loads((ROOT / "examples" / "sample-machine.json").read_text(encoding="utf-8"))
    assert "hostname" not in machine
    assert "username" not in machine
    assert "serial_number" not in json.dumps(machine).lower()
