from __future__ import annotations

import csv
import re
from pathlib import Path


_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$")


def read_model_tags(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "tag" not in reader.fieldnames:
            raise ValueError("Model CSV must have a 'tag' column")
        tags: list[str] = []
        seen: set[str] = set()
        for row_number, row in enumerate(reader, 2):
            tag = (row.get("tag") or "").strip()
            if not tag or tag.startswith("#"):
                continue
            if not _TAG.fullmatch(tag):
                raise ValueError(f"Invalid Ollama model tag at CSV row {row_number}: {tag!r}")
            if tag.lower().endswith((":cloud", "-cloud")) or ":cloud-" in tag.lower():
                raise ValueError(f"Cloud model tags are disabled by default: {tag}")
            if tag not in seen:
                tags.append(tag)
                seen.add(tag)
    if not tags:
        raise ValueError("Model CSV contains no model tags")
    return tags


def installed_tags(models_response: dict) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for model in models_response.get("models", []):
        name = model.get("name") or model.get("model")
        if name:
            found[name] = model
    return found


def comparable_tag(tag: str) -> str:
    """Ollama treats an omitted tag as :latest; match either spelling."""
    if ":" not in tag.rsplit("/", 1)[-1]:
        return tag + ":latest"
    return tag
