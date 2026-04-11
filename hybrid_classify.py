from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

import pandas as pd
import tiktoken

# -----------------------------------------------------------------------------
# 1. Setup & Configuration
# -----------------------------------------------------------------------------
sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from helper import CSV_DIR

    CSV_DIR.mkdir(exist_ok=True)
except ImportError:
    CSV_DIR = Path("results")
    CSV_DIR.mkdir(exist_ok=True)

ENC = tiktoken.encoding_for_model("gpt-4")

# Copilot CLI command.
# Change this if your command is different on your machine.
COPILOT_CMD = os.getenv("COPILOT_CMD", "gh")

# Max body tokens to send to Copilot
BODY_TOKEN_CAP = 1200

# Retry config
COPILOT_MAX_RETRIES = 3
COPILOT_RETRY_SLEEP = 3

# -----------------------------------------------------------------------------
# 2. Definitions
# -----------------------------------------------------------------------------
TYPES = {
    "feat": "A new feature",
    "fix": "A bug fix",
    "docs": "Documentation only changes",
    "style": "Changes that do not affect the meaning of the code (white-space, formatting, etc)",
    "refactor": "A code change that neither fixes a bug nor adds a feature",
    "perf": "A code change that improves performance",
    "test": "Adding missing tests or correcting existing tests",
    "build": "Changes that affect the build system or external dependencies",
    "ci": "Changes to our CI configuration files and scripts",
    "chore": "Changes to the build process or auxiliary tools",
    "other": "Any other changes that do not fit the above categories",
    "revert": "Reverts a previous commit",
}

PATTERNS = {
    t: re.compile(rf"^{t}(\([^)]*\))?!?(?=\W|$)", flags=re.IGNORECASE)
    for t in TYPES.keys()
}

ALLOWED_LABELS = set(TYPES.keys())

# -----------------------------------------------------------------------------
# 3. Helper Functions
# -----------------------------------------------------------------------------
def load_prs(source: str) -> pd.DataFrame:
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix == ".csv":
        return pd.read_csv(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported file type: {path.suffix}")


def title_label(title: str) -> str | None:
    if not title or not isinstance(title, str):
        return None
    first = title.splitlines()[0]
    for typ, pattern in PATTERNS.items():
        if pattern.match(first):
            return typ.lower()
    return None


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    text = str(text or "")
    toks = ENC.encode(text, disallowed_special=())
    return text if len(toks) <= max_tokens else ENC.decode(toks[:max_tokens])


def extract_json_object(text: str) -> dict:
    """
    Tries to extract the first JSON object from raw CLI output.
    """
    text = text.strip()

    # direct parse first
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # fallback: find first {...}
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in Copilot output")

    obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("Parsed JSON is not an object")
    return obj


def validate_result(data: dict) -> tuple[str, str, int]:
    reason = str(data.get("reason", "")).strip()
    output = str(data.get("output", "")).strip().lower()
    confidence = data.get("confidence", 0)

    if output not in ALLOWED_LABELS:
        raise ValueError(f"Invalid label returned: {output}")

    try:
        confidence = int(confidence)
    except Exception as e:
        raise ValueError(f"Invalid confidence: {confidence}") from e

    if not (1 <= confidence <= 10):
        raise ValueError(f"Confidence out of range: {confidence}")

    if not reason:
        reason = "No reason provided."

    return reason, output, confidence


def build_prompt(title: str, body: str) -> str:
    safe_body = truncate_to_tokens(body or "", BODY_TOKEN_CAP)
    types_str = "\n".join([f"{k}: {v}" for k, v in TYPES.items()])

    return f"""
You are a Conventional Commit classifier.

Given a pull request title and body, choose exactly one label from this list:

{types_str}

Return ONLY valid JSON with this exact schema:
{{
  "reason": "brief explanation",
  "output": "one of: {", ".join(TYPES.keys())}",
  "confidence": 1
}}

Rules:
- Output must be exactly one label from the allowed list.
- Confidence must be an integer from 1 to 10.
- Do not output markdown.
- Do not output explanations outside JSON.

Title:
{title}

Body:
{safe_body}
""".strip()


def call_copilot(prompt: str) -> str:
    """
    Uses GitHub Copilot CLI through gh.
    Updated for new Copilot CLI syntax (2024+)

    Uses: gh copilot -p "<prompt>" --model gpt-5-mini --silent --allow-all
    """
    cmd = [
        COPILOT_CMD, "copilot",
        "-p", prompt,
        "--model", "gpt-5-mini",
        "--silent",
        "--allow-all"
    ]

    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        raise RuntimeError(
            f"Copilot CLI failed (exit={completed.returncode}): {completed.stderr.strip()}"
        )

    stdout = completed.stdout.strip()
    if not stdout:
        raise RuntimeError("Copilot CLI returned empty output")

    return stdout


def classify_with_copilot(title: str, body: str) -> tuple[str, str, int]:
    """
    Stage 2: Copilot CLI Classification.
    Returns (reason, output, confidence).
    """
    prompt = build_prompt(title, body)

    last_error = None
    for attempt in range(1, COPILOT_MAX_RETRIES + 1):
        try:
            raw = call_copilot(prompt)
            data = extract_json_object(raw)
            return validate_result(data)
        except Exception as e:
            last_error = e
            if attempt < COPILOT_MAX_RETRIES:
                time.sleep(COPILOT_RETRY_SLEEP)

    raise RuntimeError(f"Copilot classification failed after retries: {last_error}")


# -----------------------------------------------------------------------------
# 4. Main Processing Flow
# -----------------------------------------------------------------------------
def classify_agent_prs(df: pd.DataFrame, agent: str) -> None:
    out_fp = CSV_DIR / f"{agent}_pr_task_type.csv"

    if out_fp.exists():
        result_df = pd.read_csv(out_fp)
    else:
        result_df = pd.DataFrame(columns=["agent", "id", "title", "reason", "type", "confidence"])

    done_ids = set(result_df["id"].astype(str))
    df = df[~df["id"].astype(str).isin(done_ids)].copy()

    if df.empty:
        print(f"All PRs already processed for {agent}")
        return

    df["title"] = df["title"].fillna("").astype(str)
    df["body"] = df["body"].fillna("").astype(str)

    # -------- Stage 1: Title-based Labeling --------
    df["type"] = df["title"].apply(title_label)
    df["reason"] = df["type"].apply(
        lambda x: "title provides conventional commit label" if pd.notnull(x) else None
    )
    df["confidence"] = df["type"].apply(lambda x: 10 if pd.notnull(x) else None)

    title_labeled = df[df["type"].notnull()]
    llm_needed = df[df["type"].isnull()]

    print(f"[{agent}] {len(title_labeled)} labeled via title, {len(llm_needed)} sent to Copilot CLI")

    rows = title_labeled.assign(agent=agent).to_dict(orient="records")
    if rows:
        result_df = pd.concat([result_df, pd.DataFrame(rows)], ignore_index=True)
        result_df.to_csv(out_fp, index=False)

    if llm_needed.empty:
        return

    def process_row(idx, row):
        try:
            title = row.get("title", "") or ""
            body = row.get("body", "") or ""
            pid = str(row["id"])

            reason, label, confidence = classify_with_copilot(title, body)
            print(f"[{pid}] -> {label}: {reason[:60]}... (conf {confidence})")

            return {
                "agent": agent,
                "id": pid,
                "title": title,
                "reason": reason,
                "type": label,
                "confidence": confidence,
            }
        except Exception as e:
            print(f"Error processing {row.get('id')}: {e}")
            return None

    buffer = []

    # Increased workers for faster processing. Adjust if hitting rate limits.
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_idx = {
            executor.submit(process_row, idx, row): idx
            for idx, row in llm_needed.iterrows()
        }

        for future in as_completed(future_to_idx):
            result = future.result()
            if result:
                buffer.append(result)

            if len(buffer) >= 25:
                result_df = pd.concat([result_df, pd.DataFrame(buffer)], ignore_index=True)
                result_df.to_csv(out_fp, index=False)
                print(f"Saved 25 Copilot-labeled PRs to {out_fp}")
                buffer = []

    if buffer:
        result_df = pd.concat([result_df, pd.DataFrame(buffer)], ignore_index=True)
        result_df.to_csv(out_fp, index=False)
        print(f"Saved remaining {len(buffer)} Copilot-labeled PRs to {out_fp}")


def main(source: str, agent: str | None = None) -> None:
    print(f"Loading data from {source}...")
    df = load_prs(source)
    agents: Iterable[str] = [agent] if agent else sorted(df["agent"].dropna().unique())

    for a in agents:
        print(f"Processing agent: {a}")
        sub = df[df["agent"] == a]
        if sub.empty:
            print(f"No PRs found for {a}")
            continue
        classify_agent_prs(sub, a)


if __name__ == "__main__":
    agent_arg = sys.argv[1] if len(sys.argv) > 1 else None
    dataset_path = sys.argv[2] if len(sys.argv) > 2 else "filtered_prs.parquet"
    main(dataset_path, agent_arg)
