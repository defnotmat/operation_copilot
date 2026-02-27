import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from storage import init_db, insert_event

from flask import Flask, jsonify, render_template, request
from keyword_retrieval.retrieval import (
    format_manual_context,
    load_manual_json_chunks,
    retrieve_top_manual_chunks,
)
from notion.notion import send_feature_request_to_notion

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "knowledge_base"
MESSAGES_PATH = ASSETS_DIR / "messages.json"
SCHEMA_PATH = ASSETS_DIR / "extraction_schema.json"
ENV_PATH = BASE_DIR / ".env"
PROMPT_PATH = ASSETS_DIR / "prompt.json"
MANUAL_JSON_PATH = ASSETS_DIR / "manual.json"

init_db()

app = Flask(__name__)


class ConfigError(Exception):
    pass


_OPENAI_CLIENT: Optional[Any] = None
_OPENAI_API_KEY_IN_USE: Optional[str] = None


def load_env_file() -> None:
    # Prefer python-dotenv if installed, fallback to a tiny parser.
    if load_dotenv is not None:
        load_dotenv(dotenv_path=ENV_PATH, override=False)
        return

    if not ENV_PATH.exists():
        return

    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


EXTRACTION_SCHEMA = load_json(SCHEMA_PATH)
PROMPTS = load_json(PROMPT_PATH)
SYSTEM_PROMPT = str(PROMPTS.get("default_system_prompt", "")).strip()
MESSAGES_DB = load_json(MESSAGES_PATH)

# Load manual chunks once at startup.
MANUAL_JSON_CHUNKS = load_manual_json_chunks(MANUAL_JSON_PATH)
load_env_file()


def normalize_type_items(items: Any) -> List[Dict[str, Any]]:
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if isinstance(items, dict):
        return [items]
    return []


def flatten_messages(messages_db: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for request_type, items in messages_db.items():
        for item in normalize_type_items(items):
            row = dict(item)
            row["request_type"] = request_type
            rows.append(row)
    return rows


def find_message(
    *,
    messages_db: Dict[str, Any],
    request_type: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if request_type:
        for item in normalize_type_items(messages_db.get(request_type)):
            if not message_id or item.get("id") == message_id:
                out = dict(item)
                out["request_type"] = request_type
                return out

    if message_id:
        for req_type, items in messages_db.items():
            for item in normalize_type_items(items):
                if item.get("id") == message_id:
                    out = dict(item)
                    out["request_type"] = req_type
                    return out

    return None


def resolve_input(payload: Dict[str, Any]) -> Dict[str, Any]:

    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else payload

    request_type = selected.get("request_type")
    message_id = selected.get("id")

    # If a full message object is passed, use it directly.
    if selected.get("message") and selected.get("sender") and selected.get("source"):
        if request_type:
            return selected

        guessed = find_message(messages_db=MESSAGES_DB, message_id=message_id)
        if guessed:
            selected["request_type"] = guessed["request_type"]
        return selected

    found = find_message(messages_db=MESSAGES_DB, request_type=request_type, message_id=message_id)
    if not found:
        raise ValueError("Could not resolve selected input. Send `id` (and optional `request_type`) or full message object.")
    return found


def build_extraction_prompt(selected_message: Dict[str, Any], schema: Dict[str, Any]) -> str:
    return (
        "Schema:\n"
        f"{json.dumps(schema, ensure_ascii=True)}\n\n"
        "Customer message input:\n"
        f"{json.dumps(selected_message, ensure_ascii=True)}"
    )


def parse_request_type_options(schema: Dict[str, Any]) -> List[str]:
    raw = str(schema.get("request_type", ""))
    return [item.strip() for item in raw.split("|") if item.strip()]


def get_openai_client() -> Any:
    global _OPENAI_CLIENT, _OPENAI_API_KEY_IN_USE
    if OpenAI is None:
        raise ConfigError("openai package is not installed. Install with: pip install openai")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ConfigError("OPENAI_API_KEY is missing.")

    if _OPENAI_CLIENT is not None and _OPENAI_API_KEY_IN_USE == api_key:
        return _OPENAI_CLIENT

    _OPENAI_CLIENT = OpenAI(api_key=api_key)
    _OPENAI_API_KEY_IN_USE = api_key
    return _OPENAI_CLIENT


QUESTION_REPLY_SYSTEM_PROMPT = str(
    PROMPTS.get("question_reply_system_prompt")
    or (
        "You are a support reply assistant.\n"
        "Use only the provided manual chunks as factual source.\n"
        "Do not invent plan details, pricing, or roadmap commitments.\n"
        "If the manual does not fully answer, state uncertainty briefly and ask a concise follow-up.\n"
        "IMPORTANT: If the question is not related to the manual at all, or the information is not there, then return No information found.\n"
        "Leave all other keys in the schema like Summary, Priority Rationale empty.\n"
        "Return JSON only."
    )
).strip()

FEATURE_FOLLOWUP_ACTION = str(
    PROMPTS.get("feature_followup_action")
    or (
        'Reach out to customer with the reply: "Thank you for sharing this request. '
        'We will review it with our product team, evaluate feasibility, and follow up with any updates."'
    )
).strip()

BUG_NEXT_ACTION_SYSTEM_PROMPT = str(
    PROMPTS.get("bug_next_action_system_prompt")
    or (
        "You write `suggested_next_action` for support bug reports.\n"
        "Use only the provided manual chunks as source of truth.\n"
        "Prioritize client troubleshooting steps from the chunks.\n"
        "Do not mention ticket creation, queues, triage, escalation, or internal workflow.\n"
        "Do not ask for workspace/account/client IDs, screenshots, error IDs, or logs in this field.\n"
        "Return JSON only.\n"
        'Format: {"suggested_next_action":"string"}\n'
        "If chunks do not contain a concrete action, return exactly: No suggested action"
    )
).strip()


REQUIRED_KEYS = ["request_type", "priority", "summary", "suggested_next_action", "missing_info_questions"]


def normalize_request_type(value: Any) -> str:
    token = str(value or "").strip().lower()
    aliases = {
        "bug": "bug_report",
        "bug complaint": "bug_report",
        "bug report": "bug_report",
        "bug_complaint": "bug_report",
        "bug_report": "bug_report",
        "feature": "feature_request",
        "feature request": "feature_request",
        "feature_request": "feature_request",
        "question": "question",
    }
    return aliases.get(token, token)


def retrieve_manual_chunks_for_text(text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    return retrieve_top_manual_chunks(
        text,
        MANUAL_JSON_CHUNKS,
        top_k=top_k,
        require_overlap=True,
    )


def llm_json(client: Any, model: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


def validate_extraction(extraction: Dict[str, Any]) -> Dict[str, Any]:
    missing = [key for key in REQUIRED_KEYS if key not in extraction]
    if missing:
        raise ValueError(f"LLM output missing keys: {missing}")
    return extraction


def build_route_prompt(
    req_type: str,
    extraction_prompt: str,
    chunks: List[Dict[str, str]],
) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt) for the route-specific LLM call."""
    manual_context = format_manual_context(chunks)

    if req_type == "question":
        system = ( f"{SYSTEM_PROMPT}\n\n"
                    f"{QUESTION_REPLY_SYSTEM_PROMPT}\n\n"
                    ).strip()
        user = (
           "Structured extraction:\n"
            f"{extraction_prompt}\n\n"
            "Manual chunks (ground truth):\n"
            f"{manual_context}\n\n"
        )
        return system, user

    if req_type == "bug_report":
        system =  ( f"{SYSTEM_PROMPT}\n\n"
                    f"{BUG_NEXT_ACTION_SYSTEM_PROMPT}\n\n"
                    ).strip()

        user = (
            "Structured extraction:\n"
            f"{extraction_prompt}\n\n"
            "Relevant manual chunks:\n"
            f"{manual_context}\n\n"
        )
        return system, user

    if req_type == "feature_request":
        system = SYSTEM_PROMPT
        user = (
            "Structured extraction:\n"
            f"{extraction_prompt}\n\n"
        )

        return system, user

    raise ValueError(f"Unsupported request type for route prompt: {req_type}")


def build_bug_ticket_entry(selected_message: Dict[str, Any], extraction: Dict[str, Any]) -> Dict[str, Any]:
    priority = extraction.get("priority") if isinstance(extraction.get("priority"), dict) else {}
    missing = extraction.get("missing_info_questions")
    message_id = str(selected_message.get("id", "unknown")).replace("_", "-").upper()
    return {
        "template": "internal_bug_ticket",
        "ticket_id": f"{message_id}",
        "status": "New",
        "queue": "Support Engineering",
        "priority": priority.get("level", "P2"),
        "title": extraction.get("summary", "No summary"),
        "reporter": selected_message.get("sender", "unknown"),
        "source": selected_message.get("source", "unknown"),
        "original_message_id": selected_message.get("id", "unknown"),
        "customer_message": selected_message.get("message", ""),
        "triage_rationale": priority.get("rationale", "no information found"),
        "next_internal_action": extraction.get("suggested_next_action", "no information found"),
        "missing_info_checklist": missing if isinstance(missing, list) else "All information is present",
    }


def build_feature_task_sheet(selected_message: Dict[str, Any], extraction: Dict[str, Any]) -> Dict[str, Any]:
    priority = extraction.get("priority") if isinstance(extraction.get("priority"), dict) else {}
    missing = extraction.get("missing_info_questions")
    message_id = str(selected_message.get("id", "unknown")).replace("_", "-").upper()
    return {
        "template": "product_feature_task_sheet",
        "task_id": f"{message_id}",
        "workspace": "Product Requests Board",
        "status": "Inbox",
        "owner_team": "Product Ops",
        "impact_priority": priority.get("level", "P3"),
        "title": extraction.get("summary", "No summary"),
        "requester": selected_message.get("sender", "unknown"),
        "source": selected_message.get("source", "unknown"),
        "problem_statement": selected_message.get("message", ""),
        "proposed_next_step": extraction.get("suggested_next_action", "No information found"),
        "discovery_questions": missing if isinstance(missing, list) else "All information is present",
        "tags": ["customer-feedback", str(selected_message.get("source", "unknown"))],
    }


def route_and_enrich(
    selected_message: Dict[str, Any],
    log_step: Optional[Any] = None,
) -> Dict[str, Any]:
    client = get_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    extraction_prompt = build_extraction_prompt(selected_message, EXTRACTION_SCHEMA)
    if log_step:
        log_step("classification_prompt_ready", "Structured extraction prompt prepared.")
        log_step("classification_llm_start", f"Calling model: {model}")

    req_type = str(selected_message.get("request_type", "question"))
    text = str(selected_message.get("message", ""))
    chunks = retrieve_manual_chunks_for_text(text, top_k=3)

    if log_step:
        ids = ", ".join([chunk.get("id", "UNKNOWN") for chunk in chunks]) or "none"
        log_step("manual_retrieval_done", f"Retrieved manual chunks: {ids}")

    system_prompt, user_prompt = build_route_prompt(req_type, extraction_prompt, chunks)
    routed = llm_json(client, model, system_prompt, user_prompt)

    if req_type == "bug_report":
        out = build_bug_ticket_entry(selected_message, routed)
        out["grounding_chunk_ids"] = [chunk.get("id", "UNKNOWN") for chunk in chunks]
        return {
            "request_type": req_type,
            "extraction": routed,
            "manual_chunks": chunks,
            "outcome_type": "ticket_entry",
            "outcome": out,
            "model": model,
        }

    reply = str(
        routed.get("reply") or "").strip()
    if not reply:
        reply = "No information found."

    no_info = reply.lower().rstrip(".") == "no information found"
    grounding = routed.get("grounding_chunk_ids")
    if no_info:
        grounding = []
    elif not isinstance(grounding, list) or not grounding:
        grounding = [chunk.get("id", "UNKNOWN") for chunk in chunks]
    reply_payload = {
        "to": selected_message.get("sender", "unknown"),
        "channel": selected_message.get("source", "unknown"),
        "message_id": selected_message.get("id", "unknown"),
        "reply": reply,
        "grounding_chunk_ids": [str(item) for item in grounding],
        "no_information_found": no_info,
    }
    return {
        "request_type": "question",
        "extraction": routed,
        "manual_chunks": chunks,
        "outcome_type": "reply_draft",
        "outcome": reply_payload,
        "model": model,
    }


def run_pipeline(payload: Dict[str, Any], log_step: Optional[Any] = None) -> Dict[str, Any]:
    print(payload)
    selected_message = resolve_input(payload)
    routed = route_and_enrich(selected_message, log_step=log_step)

    return {
        "input": selected_message,
        "extraction": routed["extraction"],
        "request_type": routed["request_type"],
        "outcome_type": routed["outcome_type"],
        "outcome": routed["outcome"],
        "manual_chunks": routed.get("manual_chunks", []),
        "model": routed["model"],
    }


@app.get("/")
def index() -> Any:
    return render_template("index.html")


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True})


@app.get("/schema")
def schema() -> Any:
    return jsonify(
        {
            "schema": EXTRACTION_SCHEMA,
            "request_type_options": parse_request_type_options(EXTRACTION_SCHEMA),
        }
    )


@app.get("/messages")
def list_messages() -> Any:
    return jsonify(flatten_messages(MESSAGES_DB))


@app.post("/extract")
def extract() -> Any:
    payload = request.get_json(silent=True) or {}
    pipeline_logs: List[Dict[str, str]] = []

    def add_log(step: str, detail: str) -> None:
        pipeline_logs.append({"step": step, "detail": detail})

    try:
        add_log("request_received", "Extraction request received from frontend.")
        pipeline_result = run_pipeline(payload, log_step=add_log)
        selected_message = pipeline_result["input"]
        extracted = pipeline_result["extraction"]
        request_type = normalize_request_type(pipeline_result["request_type"])
        outcome_type = pipeline_result["outcome_type"]
        outcome_payload = pipeline_result["outcome"]
        manual_chunks = pipeline_result.get("manual_chunks", [])

        notion_sync: Optional[Dict[str, Any]] = None
        if request_type == "bug_report" and outcome_type == "ticket_entry":
            notion_payload = dict(outcome_payload)
            notion_payload.setdefault("task_id", notion_payload.get("ticket_id", ""))
            notion_sync = send_feature_request_to_notion(
                selected_message=selected_message,
                extraction=extracted,
                task_sheet=notion_payload,
                log_step=add_log,
            )

        add_log("pipeline_complete", "Request processed with route-specific outcome.")

       # if request_type in {"bug_report", "feature_request"}:
       #    insert_event(
       #      selected_message=selected_message,
       #         extraction=extracted,
       #         outcome_type=outcome_type,
       #         outcome=outcome_payload,
       #         manual_chunks=manual_chunks,
       #     )
       #     add_log("event_saved", "Saved bug/feature event to support_events.db for Streamlit dashboard.")
       # else:
       #     add_log("event_skipped", "Skipped DB event save for non-dashboard request type.")

        return jsonify(
            {
                "input": selected_message,
                "extraction": extracted,
                "request_type": request_type,
                "outcome_type": outcome_type,
                "outcome": outcome_payload,
                "manual_chunks": manual_chunks,
                "notion_sync": notion_sync,
                "model": pipeline_result["model"],
                "pipeline_logs": pipeline_logs,
            }
        )
    except (ValueError, ConfigError, json.JSONDecodeError) as e:
        add_log("pipeline_failed", f"Pipeline failed: {e}")
        return jsonify({"error": str(e), "pipeline_logs": pipeline_logs}), 400
    except Exception as e:  # pragma: no cover
        add_log("pipeline_failed", f"Unexpected error: {e}")
        return jsonify({"error": f"Unexpected error: {e}", "pipeline_logs": pipeline_logs}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
