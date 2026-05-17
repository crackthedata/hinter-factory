import argparse
import json
import math
import re
import sys
from typing import Any

try:
    import polars as pl
except ImportError:
    print("Error: The 'polars' package is required. Please install it using 'pip install polars'")
    sys.exit(1)

# --- LF Executor Logic ---
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

# --- Aggregation Logic ---
def aggregate_one(positive: int, negative: int) -> tuple[float, float, float]:
    """Compute (probability, conflict_score, entropy) from vote tallies."""
    p = (1 + positive) / (2 + positive + negative)
    if positive == 0 and negative == 0:
        conflict = 0.0
    else:
        bigger = max(positive, negative)
        smaller = min(positive, negative)
        conflict = smaller / bigger if bigger > 0 else 0.0
    if p <= 0.0 or p >= 1.0:
        entropy = 0.0
    else:
        entropy = -(p * math.log2(p) + (1 - p) * math.log2(1 - p))
    return p, conflict, entropy


def process_csv(
    config_file: str,
    input_csv: str,
    output_csv: str,
    text_column: str,
) -> None:
    # 1. Load config
    try:
        with open(config_file, "r") as f:
            project_config = json.load(f)
    except Exception as e:
        print(f"Error reading config JSON '{config_file}': {e}")
        sys.exit(1)

    tags = project_config.get("tags", [])
    if not tags:
        print(f"Error: No tags found in configuration.")
        sys.exit(1)

    # 2. Read CSV
    try:
        df = pl.read_csv(input_csv)
    except Exception as e:
        print(f"Error reading input CSV '{input_csv}': {e}")
        sys.exit(1)

    if text_column not in df.columns:
        print(f"Error: Text column '{text_column}' not found in CSV. Available columns: {df.columns}")
        sys.exit(1)

    # 3. Process each row
    texts = df[text_column].to_list()
    
    # We will build a list of probabilities for each tag
    results: dict[str, list[float]] = {tag["name"]: [] for tag in tags}

    for text in texts:
        text_str = str(text) if text is not None else ""
        
        for tag in tags:
            pos_votes = 0
            neg_votes = 0
            
            for lf in tag.get("lfs", []):
                try:
                    vote = execute_labeling_function(lf["type"], lf["config"], text_str)
                    if vote > 0:
                        pos_votes += 1
                    elif vote < 0:
                        neg_votes += 1
                except LfConfigError:
                    continue
            
            prob, _, _ = aggregate_one(pos_votes, neg_votes)
            results[tag["name"]].append(prob)

    # 4. Append results to DataFrame and save
    for tag_name, probs in results.items():
        df = df.with_columns(pl.Series(f"{tag_name}_probability", probs))

    try:
        df.write_csv(output_csv)
        print(f"Successfully wrote results to '{output_csv}'")
    except Exception as e:
        print(f"Error writing output CSV '{output_csv}': {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run standalone LF batch processing on a CSV using a static config")
    parser.add_argument("--config", required=True, help="Path to the static JSON configuration exported from hinter-factory")
    parser.add_argument("--input-csv", required=True, help="Path to input CSV file")
    parser.add_argument("--output-csv", required=True, help="Path to save the output CSV file")
    parser.add_argument("--text-column", required=True, help="Name of the column containing the text")

    args = parser.parse_args()
    process_csv(args.config, args.input_csv, args.output_csv, args.text_column)
