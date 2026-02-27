# %%
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

PROJECT_ROOT = Path.cwd().resolve()
if not (PROJECT_ROOT / "knowledge_base").exists():
    for parent in PROJECT_ROOT.parents:
        if (parent / "knowledge_base").exists():
            PROJECT_ROOT = parent
            break

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from keyword_retrieval.retrieval import build_manual_chunk_index, load_manual_json_chunks


# %%

MANUAL_JSON_PATH = PROJECT_ROOT / "knowledge_base" / "manual.json"
OUTPUT_PATH = MANUAL_JSON_PATH

MANUAL_JSON_CHUNKS = load_manual_json_chunks(MANUAL_JSON_PATH)
# Force recomputation from base text fields so this script can refresh frequencies reliably.
BASE_CHUNKS = [
    {"id": chunk.get("id", ""), "title": chunk.get("title", ""), "text": chunk.get("text", "")}
    for chunk in MANUAL_JSON_CHUNKS
]
MANUAL_CHUNK_INDEX = build_manual_chunk_index(BASE_CHUNKS)

print(f"Loaded {len(MANUAL_JSON_CHUNKS)} chunks from {MANUAL_JSON_PATH}")


# %%
def counter_to_frequency_map(counter: Counter) -> Dict[str, int]:
    return {token: int(count) for token, count in counter.items() if count > 0}


def chunk_with_token_frequencies(index_entry: Dict[str, Any]) -> Dict[str, Any]:
    chunk = dict(index_entry["chunk"])
    chunk.pop("tokens", None)
    chunk["token_frequencies"] = counter_to_frequency_map(index_entry["counter"])
    return chunk


chunks_with_token_frequencies = [chunk_with_token_frequencies(entry) for entry in MANUAL_CHUNK_INDEX]
print(f"Prepared {len(chunks_with_token_frequencies)} chunks with precomputed token frequencies")


# %%
OUTPUT_PATH.write_text(json.dumps(chunks_with_token_frequencies, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Wrote manual with token frequencies to {OUTPUT_PATH}")
print(
    "Example token frequencies for first chunk:",
    list(chunks_with_token_frequencies[0]["token_frequencies"].items())[:10] if chunks_with_token_frequencies else [],
)
