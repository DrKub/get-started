"""Sample schema describes the run directory the pipeline writes."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_schema_names_the_docs_layout():
    schema = json.loads((ROOT / "data" / "samples" / "schema.json").read_text(encoding="utf-8"))
    files = schema["files"]
    for name in (
        "manifest.json",
        "validators.json",
        "spend.json",
        "attempts.jsonl",
        "blocks/<slot>.json.gz",
        "run.log",
        "data/spend.json",
    ):
        assert name in files
