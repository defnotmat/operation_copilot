import json
import os
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import request as urlrequest


def parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _notion_rich_text(text: Any) -> List[Dict[str, Any]]:
    value = str(text or "")
    if len(value) > 1900:
        value = f"{value[:1897]}..."
    return [{"type": "text", "text": {"content": value}}]


def _notion_lines(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        if not items:
            return ""
        return "\n".join([f"- {item}" for item in items])
    return str(value or "")


def send_feature_request_to_notion(
    selected_message: Dict[str, Any],
    extraction: Dict[str, Any],
    task_sheet: Dict[str, Any],
    log_step: Optional[Any] = None,
) -> Dict[str, Any]:
    if not parse_bool_env("NOTION_DEMO_ENABLED", default=False):
        if log_step:
            log_step("notion_sync_skipped", "Notion sync disabled via NOTION_DEMO_ENABLED.")
        return {"sent": False, "status": "disabled"}

    token = os.getenv("NOTION_TOKEN", "").strip()
    notion_version = os.getenv("NOTION_VERSION", "2025-09-03").strip()
    parent_id = os.getenv("NOTION_DATA_SOURCE_ID", "").strip() or os.getenv("NOTION_DATABASE_ID", "").strip()
    parent_type = "data_source_id" if os.getenv("NOTION_DATA_SOURCE_ID", "").strip() else "database_id"

    if not token or not parent_id:
        if log_step:
            log_step("notion_sync_skipped", "Notion token or parent id missing.")
        return {"sent": False, "status": "missing_config"}

    priority = extraction.get("priority") if isinstance(extraction.get("priority"), dict) else {}
    payload: Dict[str, Any] = {
        "parent": {"type": parent_type, parent_type: parent_id},
        "properties": {
            "Title": {"title": _notion_rich_text(task_sheet.get("title", "Feature request"))},
            "Request ID": {"rich_text": _notion_rich_text(task_sheet.get("task_id", ""))},
            "Priority": {"select": {"name": str(priority.get("level", "P3"))}},
            "Priority Rationale": {"rich_text": _notion_rich_text(priority.get("rationale", ""))},
            "Sender": {"rich_text": _notion_rich_text(selected_message.get("sender", ""))},
            "Source": {"rich_text": _notion_rich_text(selected_message.get("source", ""))},
            "Suggested Steps": {"rich_text": _notion_rich_text(extraction.get("suggested_next_action", ""))},
            "Questions": {"rich_text": _notion_rich_text(_notion_lines(extraction.get("missing_info_questions", [])))},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url="https://api.notion.com/v1/pages",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": notion_version,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    if log_step:
        log_step("notion_sync_start", f"Sending feature task to Notion ({parent_type}).")
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
        if log_step:
            log_step("notion_sync_done", "Feature task synced to Notion.")
        return {
            "sent": True,
            "status": "ok",
            "notion_page_id": data.get("id", ""),
            "notion_url": data.get("url", ""),
        }
    except urlerror.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            detail = str(e)
        if log_step:
            log_step("notion_sync_failed", f"Notion HTTP error: {e.code}")
        return {"sent": False, "status": "http_error", "code": e.code, "detail": detail}
    except Exception as e:  # pragma: no cover
        if log_step:
            log_step("notion_sync_failed", f"Notion sync error: {e}")
        return {"sent": False, "status": "error", "detail": str(e)}
