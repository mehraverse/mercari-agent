""""Utility functions for Mercari Agent."""
import re
from dataclasses import asdict
from typing import Any, Dict, List

from .models import ProductFull


def tokenize(text: str) -> List[str]:
    """Simple tokenizer for EN/JA text used for relevance scoring."""
    tokens = re.findall(r"[A-Za-z0-9ぁ-んァ-ン一-龥ー]+", text.lower())
    seen = set()
    uniq: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def serialize_product(p: ProductFull) -> Dict[str, Any]:
    """Convert ProductFull (including all ProductShallow fields) to JSON-serializable dict."""
    data = asdict(p)

    # Ensure we always have a usable Mercari URL when possible.
    url = data.get("url")
    if not url and data.get("id") and data.get("item_type") == "ITEM_TYPE_MERCARI":
        data["url"] = f"https://jp.mercari.com/item/{data['id']}"

    return data


def message_to_dict(msg: Any) -> Dict[str, Any]:
    """Convert OpenAI chat message to a plain dict we can keep in history."""
    payload: Dict[str, Any] = {"role": msg.role, "content": msg.content}
    if getattr(msg, "tool_calls", None):
        payload["tool_calls"] = []
        for tc in msg.tool_calls:
            payload["tool_calls"].append(
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
            )
    return payload
