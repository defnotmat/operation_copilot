import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request
from chunk.chunk import (
    build_manual_chunk_index,
    format_manual_context,
    load_manual_json_chunks,
    retrieve_top_manual_chunks,
)

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "support_knowledge_base"
MESSAGES_PATH = ASSETS_DIR / "messages.json"
SCHEMA_PATH = ASSETS_DIR / "extraction_schema.json"
ENV_PATH = BASE_DIR / ".env"
PROMPT_PATH = ASSETS_DIR / "prompt.json"
MANUAL_JSON_PATH = ASSETS_DIR / "manual.json"

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
MANUAL_JSON_CHUNKS = load_manual_json_chunks(MANUAL_JSON_PATH)
MANUAL_CHUNK_INDEX = build_manual_chunk_index(MANUAL_JSON_CHUNKS)
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
    # Supports frontend payloads like:
    # 1) {"request_type": "bug_report", "id": "bug_001"}
    # 2) {"id": "bug_001"}
    # 3) {"selected": {...full object...}}
    # 4) direct full object in payload
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
        "Return plain text only (no JSON).\n"
        "If chunks do not contain a concrete action, return exactly: no suggested action"
    )
).strip()


def extract_with_llm(selected_message: Dict[str, Any], log_step: Optional[Any] = None) -> Dict[str, Any]:
    client = get_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    prompt = build_extraction_prompt(selected_message, EXTRACTION_SCHEMA)
    if log_step:
        log_step("classification_prompt_ready", "Structured extraction prompt prepared.")
        log_step("classification_llm_start", f"Calling model: {model}")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    if log_step:
        log_step("classification_llm_done", "Structured extraction received.")

    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if log_step:
        log_step("classification_json_parse_done", "Extraction JSON parsed.")

    required = ["request_type", "priority", "summary", "suggested_next_action", "missing_info_questions"]
    missing = [k for k in required if k not in parsed]
    if missing:
        raise ValueError(f"LLM output missing keys: {missing}")
    if log_step:
        log_step("classification_schema_check_done", "Extraction schema validated.")

    return {"extraction": parsed}


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


def retrieve_bug_manual_chunks(selected_message: Dict[str, Any], log_step: Optional[Any] = None) -> List[Dict[str, str]]:
    customer_text = str(selected_message.get("message", ""))
    retrieved_chunks = retrieve_top_manual_chunks(
        customer_text,
        MANUAL_JSON_CHUNKS,
        top_k=3,
        require_overlap=True,
        chunk_index=MANUAL_CHUNK_INDEX,
    )
    if log_step:
        ids = ", ".join([chunk.get("id", "UNKNOWN") for chunk in retrieved_chunks]) or "none"
        log_step("bug_manual_retrieval_done", f"Retrieved manual chunks for bug next steps: {ids}")
    return retrieved_chunks


def build_bug_next_action_from_manual(
    selected_message: Dict[str, Any],
    extraction: Dict[str, Any],
    retrieved_chunks: Optional[List[Dict[str, str]]] = None,
    log_step: Optional[Any] = None,
) -> str:
    if retrieved_chunks is None:
        retrieved_chunks = retrieve_bug_manual_chunks(selected_message, log_step=log_step)

    if not retrieved_chunks:
        if log_step:
            log_step("bug_manual_not_found", "No relevant manual context found for bug next action.")
        return "no suggested action"

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = get_openai_client()
    prompt = (
        "Customer bug message:\n"
        f"{str(selected_message.get('message', '')).strip()}\n\n"
        "Relevant manual chunks:\n"
        f"{format_manual_context(retrieved_chunks)}\n\n"
        "Write one concise suggested_next_action for the support team.\n"
        "Focus on concrete troubleshooting actions from the chunks.\n"
        "Do not include ticket/escalation wording.\n"
        "Do not ask for IDs, screenshots, error IDs, or logs.\n"
        "If no concrete action exists in the chunks, return exactly: no suggested action"
    )
    if log_step:
        log_step("bug_next_action_llm_start", f"Calling model: {model}")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": BUG_NEXT_ACTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    if log_step:
        log_step("bug_next_action_llm_done", "Generated bug suggested_next_action from manual chunks.")

    text = (response.choices[0].message.content or "").strip()
    fallback = build_bug_next_action_from_chunks_fallback(retrieved_chunks)
    if not text:
        return fallback
    if should_reject_bug_next_action(text):
        if log_step:
            log_step("bug_next_action_filtered", "Filtered non-manual/non-actionable bug next action. Using chunk-based fallback.")
        return fallback
    return text


def build_bug_next_action_from_chunks_fallback(retrieved_chunks: List[Dict[str, str]]) -> str:
    if not retrieved_chunks:
        return "no suggested action"

    preferred = next(
        (chunk for chunk in retrieved_chunks if str(chunk.get("id", "")).upper().startswith("KB_CLIENT_")),
        retrieved_chunks[0],
    )
    text = str(preferred.get("text", "")).strip()
    if not text:
        return "no suggested action"

    lower = text.lower()
    if "customers should" in lower:
        idx = lower.find("customers should")
        steps = text[idx + len("customers should") :].strip().lstrip(":,- ").rstrip(". ")
        if steps:
            return f"Ask the customer to {steps}."

    if "support should" in lower:
        idx = lower.find("support should")
        steps = text[idx + len("support should") :].strip().lstrip(":,- ").rstrip(". ")
        if steps:
            return f"Support should {steps}."

    first_sentence = text.split(".")[0].strip()
    if not first_sentence:
        return "no suggested action"
    return first_sentence if first_sentence.endswith(".") else f"{first_sentence}."


def should_reject_bug_next_action(text: str) -> bool:
    lowered = str(text or "").lower()
    banned_patterns = [
        "ticket draft",
        "create ticket",
        "open ticket",
        "triage",
        "queue",
        "escalat",
        "workspace id",
        "account id",
        "client id",
        "screenshot",
        "error id",
        "logs",
        "log files",
        "ask the customer for",
        "ask customer for",
        "request the customer provide",
    ]
    return any(pattern in lowered for pattern in banned_patterns)


def apply_route_specific_suggested_action(
    selected_message: Dict[str, Any],
    extraction: Dict[str, Any],
    log_step: Optional[Any] = None,
) -> Dict[str, Any]:
    updated = dict(extraction)
    req_type = normalize_request_type(updated.get("request_type") or selected_message.get("request_type"))
    updated["request_type"] = req_type
    route_manual_chunks: List[Dict[str, str]] = []

    if req_type == "bug_report":
        route_manual_chunks = retrieve_bug_manual_chunks(selected_message, log_step=log_step)
        updated["suggested_next_action"] = build_bug_next_action_from_manual(
            selected_message,
            extraction=updated,
            retrieved_chunks=route_manual_chunks,
            log_step=log_step,
        )
    elif req_type == "feature_request":
        if log_step:
            log_step("feature_action_standardized", "Applied standard professional follow-up reply for feature request.")
        updated["suggested_next_action"] = FEATURE_FOLLOWUP_ACTION

    return {"extraction": updated, "route_manual_chunks": route_manual_chunks}


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
        "proposed_next_step": extraction.get("suggested_next_action", "no information found"),
        "discovery_questions": missing if isinstance(missing, list) else "All information is present",
        "tags": ["customer-feedback", str(selected_message.get("source", "unknown"))],
    }


def build_question_reply_prompt(
    selected_message: Dict[str, Any],
    extraction: Dict[str, Any],
    manual_chunks: List[Dict[str, str]],
) -> str:
    return (
        "Customer message:\n"
        f"{json.dumps(selected_message, ensure_ascii=True)}\n\n"
        "Structured extraction:\n"
        f"{json.dumps(extraction, ensure_ascii=True)}\n\n"
        "Manual chunks from manual.json (ground truth):\n"
        f"{format_manual_context(manual_chunks)}\n\n"
        "Return JSON with keys:\n"
        '{"reply_draft":"string","grounding_chunk_ids":["string"]}\n'
        "Use only grounded facts from the manual chunks."
    )


def generate_question_reply_with_manual(
    selected_message: Dict[str, Any],
    extraction: Dict[str, Any],
    log_step: Optional[Any] = None,
) -> Dict[str, Any]:
    if not MANUAL_JSON_CHUNKS:
        raise ConfigError("manual.json is missing or empty; cannot generate grounded reply for question route.")

    customer_text = str(selected_message.get("message", ""))
    retrieved_chunks = retrieve_top_manual_chunks(
        customer_text,
        MANUAL_JSON_CHUNKS,
        top_k=3,
        require_overlap=True,
        chunk_index=MANUAL_CHUNK_INDEX,
    )
    if log_step:
        ids = ", ".join([chunk.get("id", "UNKNOWN") for chunk in retrieved_chunks]) or "none"
        log_step("question_manual_retrieval_done", f"Retrieved manual.json chunks: {ids}")

    if not retrieved_chunks:
        if log_step:
            log_step("question_manual_not_found", "No relevant manual context found for this question.")
        return {
            "to": selected_message.get("sender", "unknown"),
            "channel": selected_message.get("source", "unknown"),
            "message_id": selected_message.get("id", "unknown"),
            "reply_draft": "Information you are looking for is not found in the manual.",
            "grounding_chunk_ids": [],
            "manual_chunks": [],
            "no_information_found": True,
        }

    prompt = build_question_reply_prompt(selected_message, extraction, retrieved_chunks)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = get_openai_client()
    if log_step:
        log_step("question_reply_prompt_ready", "Question reply prompt prepared from manual.json chunks.")
        log_step("question_reply_llm_start", f"Calling model: {model}")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": QUESTION_REPLY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    if log_step:
        log_step("question_reply_llm_done", "Manual-grounded reply generated.")

    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if log_step:
        log_step("question_reply_json_parse_done", "Question reply JSON parsed.")

    reply_draft = str(parsed.get("reply_draft", "")).strip()
    if not reply_draft:
        raise ValueError("Question reply output missing `reply_draft`.")

    chunk_ids = parsed.get("grounding_chunk_ids")
    if not isinstance(chunk_ids, list) or not chunk_ids:
        chunk_ids = [chunk.get("id", "UNKNOWN") for chunk in retrieved_chunks]

    return {
        "to": selected_message.get("sender", "unknown"),
        "channel": selected_message.get("source", "unknown"),
        "message_id": selected_message.get("id", "unknown"),
        "reply_draft": reply_draft,
        "grounding_chunk_ids": [str(item) for item in chunk_ids],
        "manual_chunks": retrieved_chunks,
        "no_information_found": False,
    }


def build_empty_extraction_for_unknown_question() -> Dict[str, Any]:
    return {
        "request_type": "question",
        "priority": {"level": "", "rationale": ""},
        "summary": "",
        "suggested_next_action": "",
        "missing_info_questions": [],
    }


def build_outcome(
    selected_message: Dict[str, Any],
    extraction: Dict[str, Any],
    route_manual_chunks: Optional[List[Dict[str, str]]] = None,
    log_step: Optional[Any] = None,
) -> Dict[str, Any]:
    req_type = normalize_request_type(extraction.get("request_type") or selected_message.get("request_type"))
    route_manual_chunks = route_manual_chunks or []
    allowed = {"bug_report", "feature_request", "question"}
    if req_type not in allowed:
        req_type = normalize_request_type(selected_message.get("request_type"))
    if req_type not in allowed:
        req_type = "question"

    if req_type == "bug_report":
        if log_step:
            log_step("outcome_route_selected", "Route selected: bug_report -> internal ticket entry.")
        chunk_ids = [chunk.get("id", "UNKNOWN") for chunk in route_manual_chunks]
        ticket_payload = build_bug_ticket_entry(selected_message, extraction)
        ticket_payload["grounding_chunk_ids"] = chunk_ids
        return {
            "outcome_type": "ticket_entry",
            "request_type": req_type,
            "outcome": ticket_payload,
            "manual_chunks": route_manual_chunks,
        }
    elif req_type == "feature_request":
        if log_step:
            log_step("outcome_route_selected", "Route selected: feature_request -> task sheet.")
        return {
            "outcome_type": "task_sheet",
            "request_type": req_type,
            "outcome": build_feature_task_sheet(selected_message, extraction),
            "manual_chunks": [],
        }

    if log_step:
        log_step("outcome_route_selected", "Route selected: question -> manual-grounded customer reply.")
    reply_payload = generate_question_reply_with_manual(selected_message, extraction, log_step=log_step)
    question_chunks = reply_payload.pop("manual_chunks", [])
    return {
        "outcome_type": "reply_draft",
        "request_type": "question",
        "outcome": reply_payload,
        "manual_chunks": question_chunks,
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
        selected_message = resolve_input(payload)
        add_log("input_resolved", f"Resolved input id={selected_message.get('id', 'unknown')}.")

        llm_result = extract_with_llm(selected_message, log_step=add_log)
        action_result = apply_route_specific_suggested_action(selected_message, llm_result["extraction"], log_step=add_log)
        extracted = action_result["extraction"]
        outcome_data = build_outcome(
            selected_message,
            extracted,
            route_manual_chunks=action_result.get("route_manual_chunks", []),
            log_step=add_log,
        )
        if (
            outcome_data.get("outcome_type") == "reply_draft"
            and isinstance(outcome_data.get("outcome"), dict)
            and outcome_data["outcome"].get("no_information_found")
        ):
            extracted = build_empty_extraction_for_unknown_question()
        add_log("pipeline_complete", "Request processed with route-specific outcome.")

        return jsonify(
            {
                "input": selected_message,
                "extraction": extracted,
                "request_type": outcome_data["request_type"],
                "outcome_type": outcome_data["outcome_type"],
                "outcome": outcome_data["outcome"],
                "manual_chunks": outcome_data.get("manual_chunks", []),
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
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
