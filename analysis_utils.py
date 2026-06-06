"""
analysis_utils.py
Shared utilities for all three theme notebooks.

All notebooks import from here to ensure:
  - Consistent data loading and filtering (load once, reuse)
  - Consistent plot style and colours
  - Reusable statistical tests
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy import stats as sp_stats

# ── HuggingFace URLs ──────────────────────────────────────────────────────────
_HF = "https://huggingface.co/datasets/mabujadallah/GitHub-Agentic-PR-Dataset/resolve/main"
HF_FIX_PRS        = f"{_HF}/fix_classified_prs.parquet"
HF_COMMITS        = f"{_HF}/fix_pr_commits.parquet"
HF_COMMIT_DETAILS = f"{_HF}/fix_pr_commit_details.parquet"

# ── Constants ─────────────────────────────────────────────────────────────────
AGENTS = ["Copilot", "Cursor", "Claude_Code", "Devin"]

AGENT_COLORS: dict[str, str] = {
    "Copilot":     "#1f77b4",
    "Cursor":      "#2ca02c",
    "Claude_Code": "#ff7f0e",
    "Devin":       "#d62728",
    "Human":       "#7f7f7f",
}

# AIDev dataset coverage boundary
AIDEV_END = pd.Timestamp("2025-07-31", tz="UTC")

# Output directories
RESULTS_DIR = Path("results")
THEME1_DIR  = RESULTS_DIR / "theme1_figures"
THEME2_DIR  = RESULTS_DIR / "theme2_figures"
THEME3_DIR  = RESULTS_DIR / "theme3_figures"

for _d in [THEME1_DIR, THEME2_DIR, THEME3_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


# ── Data Loading ──────────────────────────────────────────────────────────────

def load_fix_prs() -> pd.DataFrame:
    """
    Load and preprocess bug-fix PRs from HuggingFace.

    Filters to: type='fix', state='closed', agent != 'OpenAI_Codex'.
    Adds derived columns: month, is_agent, is_merged, hours_to_merge, period.
    """
    print("Loading fix PRs from HuggingFace ...")
    df = pd.read_parquet(HF_FIX_PRS)

    df = df[(df["type"] == "fix") & (df["state"] == "closed")].copy()
    df = df[df["agent"] != "OpenAI_Codex"].copy()

    df["created_at"]     = pd.to_datetime(df["created_at"], utc=True)
    df["merged_at"]      = pd.to_datetime(df["merged_at"],  utc=True, errors="coerce")
    df["month"]          = df["created_at"].dt.to_period("M")
    df["is_agent"]       = df["source"] == "agent"
    df["is_merged"]      = df["merged_at"].notna()
    df["hours_to_merge"] = (
        (df["merged_at"] - df["created_at"]).dt.total_seconds() / 3600
    ).where(df["is_merged"])
    df["period"] = df["created_at"].apply(
        lambda x: "AIDev (Dec24-Jul25)" if x <= AIDEV_END else "Post-AIDev (Aug25-Feb26)"
    )

    n_agent = int(df["is_agent"].sum())
    n_human = int((~df["is_agent"]).sum())
    print(f"  Fix PRs loaded: {len(df):,}  |  Agent: {n_agent:,}  |  Human: {n_human:,}")
    return df


def load_commits() -> pd.DataFrame:
    """Load per-PR commit metadata."""
    print("Loading commits from HuggingFace ...")
    df = pd.read_parquet(HF_COMMITS)
    print(f"  Commits loaded: {len(df):,}")
    return df


def load_commit_details() -> pd.DataFrame:
    """Load per-file line-change details per commit."""
    print("Loading commit details from HuggingFace ...")
    df = pd.read_parquet(
        HF_COMMIT_DETAILS,
        columns=["sha", "pr_id", "filename", "additions", "deletions"],
    )
    print(f"  Commit details loaded: {len(df):,}")
    return df


def build_revision_stats(
    fix_prs: pd.DataFrame,
    commits: pd.DataFrame,
    details: pd.DataFrame,
) -> pd.DataFrame:
    """
    For every merged PR, compute:
      num_commits        – total commits on the PR
      rev_lines_added    – lines added in revision commits (all except the first)
      rev_lines_deleted  – lines deleted in revision commits

    Returns one row per merged PR, with agent/source/month/period/is_agent metadata.
    """
    merged_ids = set(fix_prs.loc[fix_prs["is_merged"], "id"])

    pr_commits = commits[commits["pr_id"].isin(merged_ids)].copy()
    cpr = pr_commits.groupby("pr_id").size().reset_index(name="num_commits")

    # Exclude the first commit per PR when calculating revision effort
    first_shas = set(pr_commits.groupby("pr_id")["sha"].first().values)
    rev_shas   = set(pr_commits["sha"]) - first_shas

    rev_agg = (
        details[details["sha"].isin(rev_shas)]
        .groupby("pr_id")
        .agg(rev_lines_added=("additions", "sum"), rev_lines_deleted=("deletions", "sum"))
        .reset_index()
    )

    stats = cpr.merge(rev_agg, on="pr_id", how="left").fillna(0)

    meta = (
        fix_prs[["id", "agent", "source", "month", "period", "is_agent"]]
        .rename(columns={"id": "pr_id"})
    )
    return stats.merge(meta, on="pr_id", how="left")


# ── Statistical Tests ─────────────────────────────────────────────────────────

def merge_rate(df: pd.DataFrame) -> tuple[int, int, float]:
    """Return (n_merged, n_total, rate_pct)."""
    n_merged = int(df["is_merged"].sum())
    n_total  = len(df)
    rate     = n_merged / n_total * 100 if n_total else 0.0
    return n_merged, n_total, rate


def chi_square(a_m: int, a_t: int, b_m: int, b_t: int) -> tuple[float, float]:
    """Chi-square test of independence on two merge-rate groups."""
    table         = np.array([[a_m, a_t - a_m], [b_m, b_t - b_m]])
    chi2, p, _, _ = sp_stats.chi2_contingency(table)
    return float(chi2), float(p)


def mann_whitney(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    """Two-sided Mann-Whitney U test."""
    u, p = sp_stats.mannwhitneyu(a.dropna(), b.dropna(), alternative="two-sided")
    return float(u), float(p)


def sig_label(p: float) -> str:
    """Return significance stars."""
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


# ── Plot Helpers ──────────────────────────────────────────────────────────────

def set_plot_style() -> None:
    """Apply consistent visual style across all themes."""
    sns.set_theme(style="whitegrid", font_scale=1.1)
    plt.rcParams.update({
        "figure.dpi":        120,
        "axes.spines.top":   False,
        "axes.spines.right": False,
    })


def save_fig(fig: plt.Figure, name: str, folder: Path) -> Path:
    """Save figure as PNG and close it."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> Saved: {path}")
    return path
