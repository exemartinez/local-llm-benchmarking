from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class MachineDiscoveryError(RuntimeError):
    pass


def discover_machine(script: Path, timeout: int = 30) -> dict:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not powershell:
        raise MachineDiscoveryError("PowerShell was not found; machine measurements are unknown")
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Json"],
            check=False, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MachineDiscoveryError(f"PowerShell discovery failed: {exc}") from exc
    if result.returncode != 0:
        raise MachineDiscoveryError(result.stderr.strip() or f"PowerShell exited with {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MachineDiscoveryError("PowerShell returned invalid JSON") from exc


def unknown_machine(reason: str) -> dict:
    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": "unknown",
        "os": {"caption": "unknown", "version": "unknown", "build": "unknown", "architecture": "unknown", "windows_11_supported": "unknown"},
        "cpu": {"name": "unknown", "logical_processors": "unknown", "cores": "unknown"},
        "memory": {"installed_mib": "unknown", "available_mib": "unknown", "monitoring": "unknown"},
        "disks": "unknown",
        "gpus": "unknown",
        "monitoring_capabilities": "unknown",
        "discovery_error": "Machine inventory is unavailable; measurements are unknown.",
        "discovery_error_type": type(reason).__name__ if not isinstance(reason, str) else "DiscoveryError",
        "privacy": "Host identifiers and full paths are intentionally omitted.",
    }
