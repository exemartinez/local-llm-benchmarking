import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "reference_candidate.py"


def run_task(task):
    environment = os.environ.copy()
    environment["BENCH_CANDIDATE"] = str(FIXTURE)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"tasks/{task}/test_public.py", f"tasks/{task}/test_final.py"],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout


def test_v1_historical_suite_runs_against_fixture():
    run_task("api-sync-v1")


def test_v2_integration_suite_runs_against_fixture():
    run_task("api-sync-v2")
