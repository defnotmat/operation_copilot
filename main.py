import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

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
PROMPT_PATH = ASSETS_DIR / "prompt.txt"
MANUAL_TXT_PATH = ASSETS_DIR / "manual.txt"
MANUAL_JSON_PATH = ASSETS_DIR / "manual.json"

app = Flask(__name__)


class ConfigError(Exception):
    pass


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


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "your",
    "are",
    "our",
    "you",
    "can",
    "not",
    "but",
    "have",
    "has",
    "was",
    "were",
    "will",
    "what",
    "when",
    "where",
    "how",
    "why",
    "about",
    "into",
    "they",
    "their",
    "them",
    "than",
    "then",
    "there",
    "here",
    "would",
    "should",
    "could",
    "please",
}
TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 2 and t not in STOPWORDS]


def normalize_manual_chunks(raw: Any) -> List[Dict[str, str]]:
    chunks: List[Dict[str, str]] = []

    if isinstance(raw, list):
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            chunk_id = str(item.get("id") or f"CHUNK_{idx:03d}")
            title = str(item.get("title", "")).strip()
            chunks.append({"id": chunk_id, "title": title, "text": text})
        return chunks

    if isinstance(raw, dict):
        for idx, (key, value) in enumerate(raw.items(), start=1):
            if isinstance(value, dict):
                text = str(value.get("text", "")).strip()
                title = str(value.get("title", "")).strip()
            else:
                text = str(value).strip()
                title = ""
            if not text:
                continue
            chunk_id = str(key or f"CHUNK_{idx:03d}")
            chunks.append({"id": chunk_id, "title": title, "text": text})
        return chunks

    return chunks


def parse_manual_text_chunks(content: str) -> List[Dict[str, str]]:
    text = content.strip()
    if not text:
        return []

    # Support JSON-formatted manual text files as well.
    try:
        return normalize_manual_chunks(json.loads(text))
    except Exception:
        pass

    blocks = [b.strip() for b in re.split(r"\n\s*-{3,}\s*\n", text) if b.strip()]
    if len(blocks) == 1:
        blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]

    chunks: List[Dict[str, str]] = []
    found_explicit_id = False

    for idx, block in enumerate(blocks, start=1):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        first = lines[0]
        chunk_id = ""
        title = ""
        body_start = 0

        patterns = [
            r"^(?:id|chunk_id|chunk)\s*[:=]\s*([A-Za-z0-9_./-]+)$",
            r"^\[([A-Za-z0-9_./-]+)\]$",
            r"^#{1,6}\s*([A-Za-z0-9_./-]+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, first, flags=re.IGNORECASE)
            if match:
                chunk_id = match.group(1).strip()
                found_explicit_id = True
                body_start = 1
                break

        if not chunk_id:
            chunk_id = f"CHUNK_{idx:03d}"

        if body_start < len(lines) and lines[body_start].lower().startswith("title:"):
            title = lines[body_start].split(":", 1)[1].strip()
            body_start += 1

        text_body = "\n".join(lines[body_start:]).strip()
        if not text_body:
            text_body = block

        chunks.append({"id": chunk_id, "title": title, "text": text_body})

    if not found_explicit_id:
        return [{"id": "CHUNK_001", "title": "", "text": text}]

    return chunks


def load_manual_chunks() -> List[Dict[str, str]]:
    if MANUAL_TXT_PATH.exists():
        return parse_manual_text_chunks(MANUAL_TXT_PATH.read_text(encoding="utf-8"))
    if MANUAL_JSON_PATH.exists():
        return normalize_manual_chunks(load_json(MANUAL_JSON_PATH))
    return []


def retrieve_top_manual_chunks(query: str, top_k: int = 3) -> List[Dict[str, str]]:
    if not MANUAL_CHUNKS:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return MANUAL_CHUNKS[:top_k]

    query_counter = Counter(query_tokens)
    scored: List[Any] = []

    for idx, chunk in enumerate(MANUAL_CHUNKS):
        haystack = f"{chunk.get('id', '')} {chunk.get('title', '')} {chunk.get('text', '')}"
        chunk_tokens = tokenize(haystack)
        chunk_counter = Counter(chunk_tokens)

        overlap = set(query_counter.keys()) & set(chunk_counter.keys())
        if not overlap:
            continue

        overlap_score = sum(min(query_counter[t], chunk_counter[t]) for t in overlap)
        breadth_bonus = len(overlap)
        score = overlap_score + breadth_bonus
        scored.append((score, idx, chunk))

    if not scored:
        return MANUAL_CHUNKS[:top_k]

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [entry[2] for entry in scored[:top_k]]


EXTRACTION_SCHEMA = load_json(SCHEMA_PATH)
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
MESSAGES_DB = load_json(MESSAGES_PATH)
MANUAL_CHUNKS = load_manual_chunks()
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


def format_manual_context(chunks: List[Dict[str, str]]) -> str:
    if not chunks:
        return "No matching manual chunks found."

    parts: List[str] = []
    for chunk in chunks:
        header = f"ID: {chunk.get('id', 'UNKNOWN')}"
        if chunk.get("title"):
            header += f" | Title: {chunk['title']}"
        parts.append(f"{header}\n{chunk.get('text', '')}")
    return "\n\n".join(parts)


def build_prompt(selected_message: Dict[str, Any], schema: Dict[str, Any], manual_chunks: List[Dict[str, str]]) -> str:
    return (
        "Schema:\n"
        f"{json.dumps(schema, ensure_ascii=True)}\n\n"
        "Relevant user manual chunks (top 3 keyword matches, use these as source of truth for factual details):\n"
        f"{format_manual_context(manual_chunks)}\n\n"
        "Customer message input:\n"
        f"{json.dumps(selected_message, ensure_ascii=True)}"
    )


def parse_request_type_options(schema: Dict[str, Any]) -> List[str]:
    raw = str(schema.get("request_type", ""))
    return [item.strip() for item in raw.split("|") if item.strip()]


def get_openai_client() -> Any:
    if OpenAI is None:
        raise ConfigError("openai package is not installed. Install with: pip install openai")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ConfigError("OPENAI_API_KEY is missing.")

    return OpenAI(api_key=api_key)


def extract_with_llm(
    selected_message: Dict[str, Any],
    log_step: Optional[Any] = None,
) -> Dict[str, Any]:
    client = get_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    customer_text = str(selected_message.get("message", ""))
    retrieved_chunks = retrieve_top_manual_chunks(customer_text, top_k=3)
    if log_step:
        ids = ", ".join([chunk.get("id", "UNKNOWN") for chunk in retrieved_chunks]) or "none"
        log_step("manual_retrieval_done", f"Retrieved manual chunks: {ids}")

    prompt = build_prompt(selected_message, EXTRACTION_SCHEMA, retrieved_chunks)
    if log_step:
        log_step("prompt_ready", "Prompt composed with schema and selected message.")

    if log_step:
        log_step("llm_request_start", f"Calling model: {model}")
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
        log_step("llm_request_done", "Model response received.")

    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if log_step:
        log_step("json_parse_done", "Response parsed into JSON.")

    # Minimal sanity check to help frontend fail fast if model drifts.
    required = ["request_type", "priority", "summary", "suggested_next_action", "missing_info_questions"]
    missing = [k for k in required if k not in parsed]
    if missing:
        raise ValueError(f"LLM output missing keys: {missing}")
    if log_step:
        log_step("schema_check_done", "Required extraction keys are present.")

    return {"extraction": parsed, "retrieved_manual_chunks": retrieved_chunks}


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
        extracted = llm_result["extraction"]
        retrieved_manual_chunks = llm_result["retrieved_manual_chunks"]
        add_log("pipeline_complete", "Extraction pipeline completed successfully.")

        return jsonify(
            {
                "input": selected_message,
                "extraction": extracted,
                "retrieved_manual_chunk_ids": [chunk.get("id", "UNKNOWN") for chunk in retrieved_manual_chunks],
                "retrieved_manual_chunks": retrieved_manual_chunks,
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
