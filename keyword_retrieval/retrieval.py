import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

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


def normalize_tokens(raw_tokens: Any) -> List[str]:
    if not isinstance(raw_tokens, list):
        return []

    tokens: List[str] = []
    for token in raw_tokens:
        token_text = str(token).strip().lower()
        if not token_text:
            continue
        if not TOKEN_RE.fullmatch(token_text):
            continue
        tokens.append(token_text)
    return tokens


def normalize_token_frequencies(raw_frequencies: Any) -> Counter:
    counter: Counter = Counter()

    if isinstance(raw_frequencies, dict):
        for raw_token, raw_count in raw_frequencies.items():
            token_text = str(raw_token).strip().lower()
            if not token_text or not TOKEN_RE.fullmatch(token_text):
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if count > 0:
                counter[token_text] += count
        return counter

    if isinstance(raw_frequencies, list):
        for item in raw_frequencies:
            if not isinstance(item, dict):
                continue
            token_text = str(item.get("token", "")).strip().lower()
            if not token_text or not TOKEN_RE.fullmatch(token_text):
                continue
            raw_count = item.get("frequency", item.get("count", 0))
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if count > 0:
                counter[token_text] += count
        return counter

    return counter


def normalize_manual_chunks(raw: Any) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []

    if isinstance(raw, list):
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            chunk_id = str(item.get("id") or f"CHUNK_{idx:03d}")
            title = str(item.get("title", "")).strip()
            chunk: Dict[str, Any] = {"id": chunk_id, "title": title, "text": text}
            precomputed_counter = normalize_token_frequencies(item.get("token_frequencies"))
            if precomputed_counter:
                chunk["token_frequencies"] = dict(precomputed_counter)
            else:
                precomputed_tokens = normalize_tokens(item.get("tokens"))
                if precomputed_tokens:
                    chunk["tokens"] = precomputed_tokens
            chunks.append(chunk)
        return chunks

    if isinstance(raw, dict):
        for idx, (key, value) in enumerate(raw.items(), start=1):
            if isinstance(value, dict):
                text = str(value.get("text", "")).strip()
                title = str(value.get("title", "")).strip()
                precomputed_counter = normalize_token_frequencies(value.get("token_frequencies"))
                precomputed_tokens = normalize_tokens(value.get("tokens"))
            else:
                text = str(value).strip()
                title = ""
                precomputed_counter = Counter()
                precomputed_tokens = []
            if not text:
                continue
            chunk_id = str(key or f"CHUNK_{idx:03d}")
            chunk = {"id": chunk_id, "title": title, "text": text}
            if precomputed_counter:
                chunk["token_frequencies"] = dict(precomputed_counter)
            elif precomputed_tokens:
                chunk["tokens"] = precomputed_tokens
            chunks.append(chunk)
        return chunks

    return chunks


def load_manual_json_chunks(manual_json_path: Path) -> List[Dict[str, Any]]:
    if not manual_json_path.exists():
        return []
    return normalize_manual_chunks(load_json(manual_json_path))


def build_manual_chunk_index(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    indexed: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        precomputed_counter = normalize_token_frequencies(chunk.get("token_frequencies"))
        if precomputed_counter:
            chunk_counter = precomputed_counter
        else:
            precomputed_tokens = normalize_tokens(chunk.get("tokens"))
            if precomputed_tokens:
                chunk_counter = Counter(precomputed_tokens)
            else:
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
    chunks: List[Dict[str, Any]],
    top_k: int = 3,
    require_overlap: bool = False,
) -> List[Dict[str, Any]]:
    if not chunks:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return [] if require_overlap else chunks[:top_k]

    query_counter = Counter(query_tokens)
    query_token_keys = set(query_counter.keys())
    scored: List[Any] = []

    for idx, chunk in enumerate(chunks):
        chunk_counter = normalize_token_frequencies(chunk.get("token_frequencies"))
        if not chunk_counter:
            continue

        overlap = query_token_keys & set(chunk_counter.keys())
        if not overlap:
            continue

        overlap_score = sum(min(query_counter[t], chunk_counter[t]) for t in overlap)
        breadth_bonus = len(overlap)
        score = overlap_score + breadth_bonus
        scored.append((score, idx, chunk))

    if not scored:
        return [] if require_overlap else chunks[:top_k]

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [entry[2] for entry in scored[:top_k]]


def format_manual_context(chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return "No matching manual chunks found."

    parts: List[str] = []
    for chunk in chunks:
        header = f"ID: {chunk.get('id', 'UNKNOWN')}"
        if chunk.get("title"):
            header += f" | Title: {chunk['title']}"
        parts.append(f"{header}\n{chunk.get('text', '')}")
    return "\n\n".join(parts)
