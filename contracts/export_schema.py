"""python -m contracts.export_schema -> contracts/schema/*.json (feeds generated TS types)."""
import json
from pathlib import Path

from pydantic import TypeAdapter

from . import models as m

OUT = Path(__file__).parent / "schema"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    items = {
        "FlipbookRequest": m.FlipbookRequest,
        "FrameRef": m.FrameRef,
        "FrameLabel": m.FrameLabel,
        "Event": TypeAdapter(m.Event),
    }
    for name, t in items.items():
        schema = t.model_json_schema() if hasattr(t, "model_json_schema") else t.json_schema()
        (OUT / f"{name}.json").write_text(json.dumps(schema, indent=2) + "\n")
    (OUT / "VERSION").write_text(m.CONTRACT_VERSION + "\n")


if __name__ == "__main__":
    main()
