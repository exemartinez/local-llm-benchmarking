# Windows 11 setup

## Prerequisites

- 64-bit Windows 11 (the first version intentionally rejects other platforms/builds).
- Python 3.11 or newer and the Python launcher (`py`).
- Ollama for Windows, running locally. Install it from [Ollama's official download page](https://ollama.com/download/windows) and follow its prompts. The benchmark does not install or update software.
- Disk headroom for model downloads. Model files can be many gigabytes. The benchmark checks free space against the configured floor, but it cannot predict every model's storage or memory requirements.

Ollama's Windows requirements and model storage options can change; see the current [Windows documentation](https://docs.ollama.com/windows). GPU acceleration and model fit depend on hardware, driver and model quantization. The machine explorer records what it can read and reports unsupported measurements as `unknown`.

## Install the project

From a PowerShell prompt opened in this project directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

If PowerShell blocks environment activation, run the commands without activation using `.\.venv\Scripts\python.exe` and `.\.venv\Scripts\ollama-bench.exe`.

Confirm the local server and project setup:

```powershell
ollama-bench doctor
```

`doctor` makes a local request to `127.0.0.1:11434`. It does not sign in, contact a cloud inference endpoint, download models, or start an Ollama service.

## Configure models

Edit `models.csv` to the exact desired local model tags:

```csv
tag
qwen3.5:4b
qwen3-coder:30b
```

These are examples only. Pulling a model can consume substantial bandwidth and disk space. Before pulling each missing tag, the runner asks for `YES`. Declining marks the model's task rows `skipped`. Existing models and files are never removed.

Adjust the resource and generation defaults in `config/benchmark.json` if needed. Low or unknown available RAM and free disk require a separate `YES` confirmation to continue. These checks are conservative operational gates, not proof that a model will fit or that an out-of-memory condition cannot occur.

## Run and resume

```powershell
ollama-bench discover-machine
ollama-bench run
```

Before tests execute a generated candidate, inspect the saved `.py` file and type `HOST`. This allows candidate code to run with your current account's access. No isolation is provided. Do not approve a candidate you have not reviewed. If you need a stronger boundary, use a separately configured Windows Sandbox or disposable VM and verify its network, file-sharing and resource settings independently.

An interrupted run prints a run ID. Resume it with:

```powershell
ollama-bench run --resume RUN_ID
```

Resume adds attempt folders and result history; it does not overwrite completed artifacts. It reads the saved run configuration, model list and host snapshot. A changed task suite is distinguished by its SHA-256 fingerprint.

## Test the benchmark software

```powershell
python -m pytest
```

Project tests use mocked Ollama and HTTP responses plus a trusted reference fixture. They do not pull models or call a paid/cloud service. A benchmark run against an actual Ollama model is separate from these software tests.
