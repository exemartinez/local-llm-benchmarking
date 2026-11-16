# Local Ollama Benchmark

Open-source, evidence-first coding benchmarks for local Ollama models on Windows 11. The project compares a user-provided list of local model tags against the same versioned task suites, sequentially, with a saved prompt, response, candidate, tests, settings, timestamps and resource observations for every attempt.

This project has no paid or cloud inference path. Model listing, downloads and inference use the local Ollama API on loopback. Cloud tags are rejected. No API credentials are needed.

## What it measures

- Windows version/build, CPU, installed and available RAM, fixed-disk capacity/free space, GPUs, VRAM, driver versions, and whether memory/GPU monitors are available.
- Ollama model tags and quantization when Ollama reports it, Ollama version, context and sampling settings, seed, API calls/retries, response token counts and Ollama timing fields.
- Wall-clock generation, public-test and final-test time, repair count, test status and periodic sanitized host resource snapshots.
- Public-test repairs are bounded. A separate final suite is run against the final candidate; final-test output is saved but never sent back to the model.

The output has a history CSV (`results.csv`), a latest-result comparison CSV (`summary.csv`), a readable Markdown report, and per-attempt directories retaining prompts, raw responses, candidate source and test output. A task suite fingerprint is recorded alongside its version so edited tests are distinguishable even if a version number was not bumped.

## Install and setup

See [INSTALL.md](INSTALL.md) for Windows 11 and Ollama setup. Runtime orchestration uses the Python standard library. Install pytest only for the test runner.

Edit [`models.csv`](models.csv) and keep the `tag` heading. Replace its sample tags with the exact local Ollama tags to compare. The program checks locally installed tags and asks before pulling any missing tag. It never removes models.

Review [`config/benchmark.json`](config/benchmark.json) before a run. Defaults include a 3 GiB available-RAM floor, 10 GiB free-disk floor, 15-minute model-call limit, two public-test repair rounds, an 8,192-token context and 4,096 maximum generated tokens. Edit the file to adjust limits; the runner never silently turns a protection off. If a resource measurement is missing or below the floor, the operator must type `YES` for that run to continue. During a long download, model call or test, periodic samples can stop the next benchmark step if RAM or disk falls below the configured floor. An in-progress Ollama request is bounded by its timeout and is not force-terminated by this monitor.

## Commands

Run these from the project directory in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
ollama-bench doctor
ollama-bench discover-machine
ollama-bench run
```

The first candidate execution pauses for review. Inspect the saved candidate file; type `HOST` to let it run under the current Windows account. `--allow-host-execution` bypasses that prompt only when explicitly supplied. This project does not create a sandbox. A venv, a subprocess, a static check, pytest or a mocked network transport is not a security boundary. Candidate code could read or change files available to the user, start processes or access the network. For isolation, review the source first and use a separately configured Windows Sandbox or disposable VM; this project does not configure one.

If interrupted, the runner saves the active attempt and results already collected. Use the run ID printed by the program to continue:

```powershell
ollama-bench run --resume 20261116T120000Z-a1b2c3
```

Completed model/task pairs are not rerun during resume. Interrupted work gets a new numbered attempt directory. Original outputs are preserved.

## Versioned tasks and integrity

- [`api-sync-v1`](tasks/api-sync-v1/) keeps the historical API-to-SQLite task contract. Earlier results on this task are historical evidence only. A permissive CLI mock concealed an integration defect in a previously passing candidate, so v1 is not a production-readiness claim.
- [`api-sync-v2`](tasks/api-sync-v2/) tests the actual CLI → fetch function → HTTP transport path. Its integration test replaces only the network transport. It also checks safe completion logging, response and SQLite connection closure, invalid configuration, later-page rollback and creation rollback.

The public suite source and failures can be included in bounded repair prompts. The final checks are separate and withheld from prompts. The runner does not report a model ranking beyond the recorded task versions, settings and host evidence. `pass` means the saved candidate passed this saved test suite in this run; it is not a general quality or safety guarantee.

Outcomes are `pass`, `fail`, `incomplete`, `skipped`, `interrupted`, and `resource_stop`. They mean respectively: final suite passed; final suite completed with failures; generation or tests did not complete; download or host execution was declined; the operator stopped the run; or a configured resource floor stopped the run.

## Privacy and model storage

Default reports omit machine name, user name, serial numbers, account identifiers and full paths. Machine inventory stores hardware names and system capacity readings, which can still identify a device in some contexts; review it before sharing. Prompts and generated code can contain private information if a task is changed, so inspect all run artifacts before publishing them. Run directories and machine snapshots are ignored by Git.

Downloads remain under Ollama's configured model storage. The benchmark does not delete, move, or clean up Ollama models, user files, or previous run folders. `models.csv` is the only model-selection source. A missing model is pulled only after explicit confirmation.

## Official references

- [Ollama Windows documentation](https://docs.ollama.com/windows)
- [Ollama local chat API](https://docs.ollama.com/api/chat)
- [Ollama list models API](https://docs.ollama.com/api/tags)
- [Ollama Windows download](https://ollama.com/download/windows)

## License

MIT. Individual Ollama models have their own licenses and terms; check those before use or redistribution.
