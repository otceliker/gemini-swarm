"""Robust JSON extraction from LLM responses (shared across the engine)."""
from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> Any:
    """Pull the first balanced JSON object/array out of an LLM response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    candidates = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not candidates:
        raise ValueError("no JSON found in response")
    start = min(candidates)
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"

    depth = 0
    in_str = esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON in response")


def safe_extract_json(text: str) -> dict:
    """extract_json, but never raises — returns {} on any failure."""
    try:
        data = extract_json(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
