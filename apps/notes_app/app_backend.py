import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("SIAMBA_OS_ROOT", ""))
import siamba_runtime as rt

DATA_DIR = Path(os.environ.get("SIAMBA_APP_DATA", ".")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
NOTES_FILE = DATA_DIR / "notes.json"


def _load() -> list:
    if not NOTES_FILE.is_file():
        return []
    try:
        return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(notes: list) -> None:
    NOTES_FILE.write_text(
        json.dumps(notes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@rt.method("list")
def list_notes():
    return {"ok": True, "notes": _load()}


@rt.method("add")
def add_note(text=""):
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty"}
    notes = _load()
    notes.insert(0, {"id": int(time.time() * 1000), "text": text})
    _save(notes)
    return {"ok": True, "notes": notes}


@rt.method("delete")
def delete_note(note_id=None):
    notes = [n for n in _load() if n.get("id") != note_id]
    _save(notes)
    return {"ok": True, "notes": notes}


if __name__ == "__main__":
    rt.run()