"""Export the Incident JSON Schema to /schema/incident.schema.json (run: python -m app.models.schema_export)."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import REPO_ROOT
from .incident import Incident

SCHEMA_PATH = REPO_ROOT / "schema" / "incident.schema.json"


def incident_schema() -> dict:
    schema = Incident.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://example.local/schema/incident.schema.json"
    return schema


def render() -> str:
    return json.dumps(incident_schema(), indent=2, sort_keys=True) + "\n"


def main(path: Path = SCHEMA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render())
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
