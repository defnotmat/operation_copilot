import json
import os
from typing import Any, Dict, Optional
import requests


def _notion_title(text: str) -> Dict[str, Any]:
    return {
        "title": [
            {
                "type": "text",
                "text": {"content": text},
            }
        ]
    }


def _notion_rich_text(text: str) -> Dict[str, Any]:
    # Notion "rich_text" property expects a list of rich-text objects
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


def _notion_rich_text_lines(value: Any) -> Dict[str, Any]:
    # Accept list[str] or anything; convert to a single rich_text field with newlines
    if value is None:
        return _notion_rich_text("")
    if isinstance(value, list):
        joined = "\n".join(str(x) for x in value if x is not None)
        return _notion_rich_text(joined)
    return _notion_rich_text(str(value))


def send_feature_request_to_notion(
    selected_message: Dict[str, Any],
    extraction: Dict[str, Any],
    task_sheet: Dict[str, Any],
    log_step: Optional[Any] = None,
) -> Dict[str, Any]:

    token = os.getenv("NOTION_TOKEN", "").strip()
    notion_version = os.getenv("NOTION_VERSION", "2025-09-03").strip()
    database_id = os.getenv("NOTION_DATABASE_ID", "").strip()

    if not token or not database_id:
        if log_step:
            log_step("notion_sync_skipped", "NOTION_TOKEN or NOTION_DATA_SOURCE_ID missing.")
        return {"sent": False, "status": "missing_config"}

    priority_obj = extraction.get("priority")
    priority = priority_obj if isinstance(priority_obj, dict) else {}
    priority_level = str(priority.get("level", "P3")).strip() or "P3"

    payload: Dict[str, Any] = {
        "parent": {"database_id": database_id},
        "properties": {
            "Title": _notion_title(str(task_sheet.get("title", "Feature request"))),
            "Request ID": _notion_rich_text(str(task_sheet.get("task_id", ""))),
            "Priority": {"select": {"name": priority_level}},
            "Priority Rationale": _notion_rich_text(str(priority.get("rationale", ""))),
            "Sender": _notion_rich_text(str(selected_message.get("sender", ""))),
            "Source": _notion_rich_text(str(selected_message.get("source", ""))),
            "Suggested Steps": _notion_rich_text(str(extraction.get("suggested_next_action", ""))),
            "Questions": _notion_rich_text_lines(extraction.get("missing_info_questions", [])),
        },
    }


    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": notion_version,
        "Content-Type": "application/json",
    }


    if log_step:
        log_step("notion_sync_start", "Sending feature task to Notion.")

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=15,  # prevent hanging
        )

        print("🔵 NOTION STATUS:", response.status_code)
        print("🔵 NOTION RESPONSE:", response.text)

        # Raise HTTPError for 4xx/5xx
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            data = {}

        if log_step:
            log_step("notion_sync_done", "Feature task synced to Notion.")

        return {
            "sent": True,
            "status": "ok",
            "notion_page_id": data.get("id", ""),
            "notion_url": data.get("url", ""),
        }

    # --- HTTP errors (4xx / 5xx from Notion) ---
    except requests.exceptions.HTTPError as http_err:
        detail = ""
        try:
            detail = response.text
        except Exception:
            detail = str(http_err)

        if log_step:
            log_step("notion_sync_failed", f"HTTP error {response.status_code}")

        return {
            "sent": False,
            "status": "http_error",
            "code": response.status_code,
            "detail": detail,
        }

    # --- Network errors (DNS, connection, timeout) ---
    except requests.exceptions.Timeout:
        if log_step:
            log_step("notion_sync_failed", "Request timed out.")
        return {"sent": False, "status": "timeout"}

    except requests.exceptions.ConnectionError:
        if log_step:
            log_step("notion_sync_failed", "Connection error.")
        return {"sent": False, "status": "connection_error"}

    except requests.exceptions.RequestException as e:
        if log_step:
            log_step("notion_sync_failed", f"Request exception: {e}")
        return {"sent": False, "status": "request_error", "detail": str(e)}

    # --- Any unexpected Python error ---
    except Exception as e:
        if log_step:
            log_step("notion_sync_failed", f"Unexpected error: {e}")
        return {"sent": False, "status": "error", "detail": str(e)}


