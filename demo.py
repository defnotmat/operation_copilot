import json
import os
from typing import Any, Dict, List

import streamlit as st

from main import (
    ConfigError,
    EXTRACTION_SCHEMA,
    MESSAGES_DB,
    build_bug_ticket_entry,
    build_extraction_prompt,
    build_route_prompt,
    get_openai_client,
    llm_json,
    retrieve_manual_chunks_for_text,
)


def bug_messages() -> List[Dict[str, Any]]:
    raw = MESSAGES_DB.get("bug_report", [])
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def default_bug_extraction(selected_message: Dict[str, Any], chunk_ids: List[str]) -> Dict[str, Any]:
    sender = str(selected_message.get("sender", "customer"))
    return {
        "request_type": "bug_report",
        "priority": {
            "level": "P1",
            "rationale": f"{sender} reports a blocking export failure with production impact.",
        },
        "summary": "Customer export jobs fail for files larger than 200MB and block a core finance workflow.",
        "reply": "",
        "suggested_next_action": (
            "Ask customer to split export size below 200MB, retry after a few minutes, and share workspace ID "
            "plus timestamp if failure continues."
        ),
        "missing_info_questions": [
            "Can you share the workspace or account ID?",
            "What is the exact timestamp and timezone of the latest failure?",
            "What file size was exported when the error happened?",
        ],
        "grounding_chunk_ids": chunk_ids,
    }


def normalize_bug_extraction(raw: Dict[str, Any]) -> Dict[str, Any]:
    priority_raw = raw.get("priority")
    priority = priority_raw if isinstance(priority_raw, dict) else {}

    missing_raw = raw.get("missing_info_questions")
    if isinstance(missing_raw, list):
        missing = [str(item) for item in missing_raw if str(item).strip()]
    elif missing_raw is None:
        missing = []
    else:
        text = str(missing_raw).strip()
        missing = [text] if text else []

    return {
        "request_type": "bug_report",
        "priority": {
            "level": str(priority.get("level", "P2")).strip() or "P2",
            "rationale": str(priority.get("rationale", "no information found")).strip() or "no information found",
        },
        "summary": str(raw.get("summary", "No summary")).strip() or "No summary",
        "reply": str(raw.get("reply", "")).strip(),
        "suggested_next_action": str(raw.get("suggested_next_action", "no suggested action")).strip() or "no suggested action",
        "missing_info_questions": missing,
    }


st.set_page_config(page_title="Bug Report End-to-End Demo", layout="wide")
st.title("Bug Report End-to-End Demo")
st.caption("From incoming bug report to prompts, LLM extraction, and final bug ticket output.")

bugs = bug_messages()
if not bugs:
    st.error("No bug_report entries found in knowledge_base/messages.json")
    st.stop()

labels = []
for item in bugs:
    labels.append(f"{item.get('id', 'NO_ID')} - {item.get('ui_label', 'Bug Report')}")

selected_label = st.selectbox("Choose a bug report example", labels)
selected_idx = labels.index(selected_label)
selected_message = dict(bugs[selected_idx])
selected_message["request_type"] = "bug_report"

st.subheader("1. Incoming Bug Report")
st.json(selected_message)

chunks = retrieve_manual_chunks_for_text(str(selected_message.get("message", "")), top_k=3)
chunk_ids = [str(chunk.get("id", "UNKNOWN")) for chunk in chunks]

st.subheader("2. Retrieved Manual Chunks")
if not chunks:
    st.warning("No manual chunks retrieved for this message.")
else:
    st.caption("Top relevant chunks used to ground the bug extraction.")
    st.json(chunks)

extraction_prompt = build_extraction_prompt(selected_message, EXTRACTION_SCHEMA)
system_prompt, user_prompt = build_route_prompt("bug_report", extraction_prompt, chunks)

col1, col2 = st.columns(2)
with col1:
    st.subheader("3. System Prompt")
    st.code(system_prompt, language="text")
with col2:
    st.subheader("4. User Prompt")
    st.code(user_prompt, language="text")

st.subheader("5. LLM Extraction Output")
run_real_llm = st.checkbox("Run with real LLM (requires OPENAI_API_KEY and network)", value=False)

extraction: Dict[str, Any]
source = "mocked demo output"

if run_real_llm:
    try:
        client = get_openai_client()
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        extraction = llm_json(client, model, system_prompt, user_prompt)
        source = f"real model output ({model})"
    except ConfigError as e:
        st.warning(f"Real LLM unavailable: {e}. Falling back to mocked demo output.")
        extraction = default_bug_extraction(selected_message, chunk_ids)
    except Exception as e:
        st.warning(f"LLM call failed: {e}. Falling back to mocked demo output.")
        extraction = default_bug_extraction(selected_message, chunk_ids)
else:
    extraction = default_bug_extraction(selected_message, chunk_ids)

normalized_extraction = normalize_bug_extraction(extraction)
st.caption(f"Source: {source}")
st.json(extraction)

ticket = build_bug_ticket_entry(selected_message, normalized_extraction)
ticket["grounding_chunk_ids"] = chunk_ids

st.subheader("6. Built Bug Ticket")
st.json(ticket)

st.subheader("7. End-to-End Summary")
summary_rows = [
    {"step": "Input", "detail": f"Bug report {selected_message.get('id', 'NO_ID')} selected."},
    {"step": "Retrieval", "detail": f"Retrieved chunks: {', '.join(chunk_ids) if chunk_ids else 'none'}"},
    {"step": "Prompting", "detail": "Built bug route system + user prompts."},
    {"step": "Extraction", "detail": f"Generated extraction from {source}."},
    {"step": "Ticket Build", "detail": f"Ticket {ticket.get('ticket_id', 'UNKNOWN')} rendered."},
]
st.table(summary_rows)
