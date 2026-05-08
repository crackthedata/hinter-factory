from __future__ import annotations

import re
from typing import Any


class LfConfigError(ValueError):
    pass


def _caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def _punctuation_ratio(text: str) -> float:
    if not text:
        return 0.0
    punct = sum(1 for c in text if (not c.isalnum()) and (not c.isspace()))
    return punct / len(text)


def _compile_regex(config: dict[str, Any]) -> re.Pattern[str]:
    pattern = config.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise LfConfigError("regex labeling functions require a non-empty string 'pattern'")
    flags = 0
    raw_flags = config.get("flags")
    if isinstance(raw_flags, str):
        if "i" in raw_flags.lower():
            flags |= re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise LfConfigError(f"invalid regex: {exc}") from exc


def execute_regex(config: dict[str, Any], text: str) -> int:
    rx = _compile_regex(config)
    match = rx.search(text)
    return_value = config.get("return_value", 1)
    if not isinstance(return_value, int) or return_value not in (-1, 0, 1):
        raise LfConfigError("regex 'return_value' must be -1, 0, or 1")
    return return_value if match else 0


def execute_keywords(config: dict[str, Any], text: str) -> int:
    keywords = config.get("keywords")
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        raise LfConfigError("keywords labeling functions require 'keywords': string[]")
    mode = str(config.get("mode", "any")).lower()
    return_value = config.get("return_value", 1)
    if not isinstance(return_value, int) or return_value not in (-1, 0, 1):
        raise LfConfigError("keywords 'return_value' must be -1, 0, or 1")
    hay = text.lower()
    kws = [k.lower() for k in keywords if k]
    if not kws:
        return 0
    if mode == "all":
        return return_value if all(k in hay for k in kws) else 0
    return return_value if any(k in hay for k in kws) else 0


def execute_structural(config: dict[str, Any], text: str) -> int:
    n = len(text)
    caps = _caps_ratio(text)
    punct = _punctuation_ratio(text)
    return_value = config.get("return_value", 1)
    if not isinstance(return_value, int) or return_value not in (-1, 0, 1):
        raise LfConfigError("structural 'return_value' must be -1, 0, or 1")

    checks: list[tuple[str, float, float]] = []

    def add(op: str, key: str, cur: float) -> None:
        if key not in config or config[key] is None:
            return
        bound = config[key]
        if not isinstance(bound, (int, float)):
            raise LfConfigError(f"structural '{key}' must be a number")
        checks.append((op, cur, float(bound)))

    add(">=", "length_gte", float(n))
    add("<=", "length_lte", float(n))
    add(">=", "caps_ratio_gte", caps)
    add("<=", "caps_ratio_lte", caps)
    add(">=", "punctuation_ratio_gte", punct)
    add("<=", "punctuation_ratio_lte", punct)

    if not checks:
        return 0

    for op, cur, bound in checks:
        if op == ">=" and not (cur >= bound):
            return 0
        if op == "<=" and not (cur <= bound):
            return 0
    return return_value


def execute_labeling_function(lf_type: str, config: dict[str, Any], text: str) -> int:
    if lf_type == "regex":
        return execute_regex(config, text)
    if lf_type == "keywords":
        return execute_keywords(config, text)
    if lf_type == "structural":
        return execute_structural(config, text)
    if lf_type in ("zeroshot", "llm_prompt"):
        return 0
    raise LfConfigError(f"unsupported labeling function type: {lf_type}")
