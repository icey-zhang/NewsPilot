# coding=utf-8
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def save_llm_run(
    *,
    output_dir: str,
    date: str,
    kind: str,
    model: str,
    payload: Dict[str, Any],
) -> str:
    """
    Persist LLM enrichment results to a separate SQLite db.

    Path: {output_dir}/llm/{date}.db
    """
    base = Path(output_dir) / "llm"
    base.mkdir(parents=True, exist_ok=True)
    db_path = base / f"{date}.db"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              kind TEXT NOT NULL,
              model TEXT NOT NULL,
              payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO llm_runs(created_at, kind, model, payload_json) VALUES (?, ?, ?, ?)",
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                kind,
                model,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
        return str(db_path)
    finally:
        conn.close()


def get_latest_llm_run(
    *,
    output_dir: str,
    date: str,
    kind: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    db_path = Path(output_dir) / "llm" / f"{date}.db"
    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if kind:
            row = conn.execute(
                "SELECT * FROM llm_runs WHERE kind=? ORDER BY id DESC LIMIT 1",
                (kind,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM llm_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "kind": row["kind"],
            "model": row["model"],
            "payload": json.loads(row["payload_json"]),
        }
    finally:
        conn.close()

