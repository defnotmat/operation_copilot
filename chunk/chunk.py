import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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


def load_manual_json_chunks(manual_json_path: Path) -> List[Dict[str, str]]:
    if not manual_json_path.exists():
        return []
    return normalize_manual_chunks(load_json(manual_json_path))


def build_manual_chunk_index(chunks: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    indexed: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        haystack = f"{chunk.get('id', '')} {chunk.get('title', '')} {chunk.get('text', '')}"
        chunk_tokens = tokenize(haystack)
        chunk_counter = Counter(chunk_tokens)
        indexed.append(
            {
                "idx": idx,
                "chunk": chunk,
                "counter": chunk_counter,
                "token_keys": set(chunk_counter.keys()),
            }
        )
    return indexed


def retrieve_top_manual_chunks(
    query: str,
    chunks: List[Dict[str, str]],
    top_k: int = 3,
    require_overlap: bool = False,
    chunk_index: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    if not chunks:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return [] if require_overlap else chunks[:top_k]

    query_counter = Counter(query_tokens)
    query_token_keys = set(query_counter.keys())
    active_index = chunk_index if chunk_index is not None else build_manual_chunk_index(chunks)
    scored: List[Any] = []

    for entry in active_index:
        overlap = query_token_keys & entry["token_keys"]
        if not overlap:
            continue

        chunk_counter = entry["counter"]
        overlap_score = sum(min(query_counter[t], chunk_counter[t]) for t in overlap)
        breadth_bonus = len(overlap)
        score = overlap_score + breadth_bonus
        scored.append((score, entry["idx"], entry["chunk"]))

    if not scored:
        return [] if require_overlap else chunks[:top_k]

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [entry[2] for entry in scored[:top_k]]


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
