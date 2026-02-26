import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent / "support_events.db"

def init_db() -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS support_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            message_id TEXT,
            sender TEXT,
            source TEXT,
            request_type TEXT,
            outcome_type TEXT,
            priority_level TEXT,
            summary TEXT,
            suggested_next_action TEXT,
            missing_info_questions TEXT,
            outcome_json TEXT,
            manual_chunk_ids TEXT
        )
        """
    )
    con.commit()
    con.close()

def safe_text(x: Any) -> str:
    return "" if x is None else str(x)

def insert_event(
    *,
    selected_message: Dict[str, Any],
    extraction: Dict[str, Any],
    outcome_type: str,
    outcome: Any,
    manual_chunks: Optional[List[Dict[str, Any]]] = None,
) -> None:
    priority = extraction.get("priority") if isinstance(extraction.get("priority"), dict) else {}
    missing = extraction.get("missing_info_questions")
    if not isinstance(missing, list):
        missing = []

    chunk_ids = []
    if manual_chunks:
        chunk_ids = [safe_text(c.get("id", "")) for c in manual_chunks if isinstance(c, dict)]

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO support_events (
            message_id, sender, source, request_type, outcome_type,
            priority_level, summary, suggested_next_action,
            missing_info_questions, outcome_json, manual_chunk_ids
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            safe_text(selected_message.get("id")),
            safe_text(selected_message.get("sender")),
            safe_text(selected_message.get("source")),
            safe_text(extraction.get("request_type")),
            safe_text(outcome_type),
            safe_text(priority.get("level")),
            safe_text(extraction.get("summary")),
            safe_text(extraction.get("suggested_next_action")),
            json.dumps(missing, ensure_ascii=True),
            json.dumps(outcome, ensure_ascii=True),
            json.dumps(chunk_ids, ensure_ascii=True),
        ),
    )
    con.commit()
    con.close()