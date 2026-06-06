"""
Simple temporal exploration of bug-fix PRs over time.
Streams the parquet in batches to keep memory low, builds one small row per PR,
then aggregates everything by month.

Questions:
  A. Volume over time  -> are people adopting agents? switching between agents?
  B. Merge rate over time (per group)
  C. Time-to-merge over time (per group)
  D. PR description length over time (do "instructions" change?)
"""
import pyarrow.parquet as pq
import pandas as pd
import numpy as np

SRC = "fix_classified_prs.parquet"
COLS = ["created_at", "merged_at", "agent", "source", "type", "state", "body", "repo_id", "user_id"]

rows = []
pf = pq.ParquetFile(SRC)
for batch in pf.iter_batches(batch_size=200_000, columns=COLS):
    b = batch.to_pandas()
    b = b[(b["type"] == "fix") & (b["state"] == "closed") & (b["agent"] != "OpenAI_Codex")]
    if b.empty:
        continue
    created = pd.to_datetime(b["created_at"], utc=True, errors="coerce")
    merged = pd.to_datetime(b["merged_at"], utc=True, errors="coerce")
    body = b["body"].fillna("")
    rows.append(pd.DataFrame({
        "month": created.dt.to_period("M").astype(str),
        "agent": b["agent"].values,
        "source": b["source"].values,
        "is_merged": merged.notna().values,
        "hours_to_merge": ((merged - created).dt.total_seconds() / 3600).values,
        "body_words": body.str.split().str.len().values,
        "repo_id": b["repo_id"].values,
        "user_id": b["user_id"].values,
    }))

df = pd.concat(rows, ignore_index=True)
print("total PRs:", len(df))

# group label: each agent separately + Human
df["group"] = np.where(df["source"] == "agent", df["agent"], "Human")

def monthly_table(value_col, aggfunc):
    t = df.pivot_table(index="month", columns="group", values=value_col, aggfunc=aggfunc)
    return t.sort_index()

# A. VOLUME -------------------------------------------------------------
vol = df.pivot_table(index="month", columns="group", values="is_merged", aggfunc="size").sort_index()
vol = vol.fillna(0).astype(int)
print("\n=== A. MONTHLY VOLUME (count of fix PRs) ===")
print(vol.to_string())

# Agent market share among agents only
agent_only = df[df["source"] == "agent"]
share = agent_only.pivot_table(index="month", columns="agent", values="is_merged", aggfunc="size").sort_index()
share = share.div(share.sum(axis=1), axis=0) * 100
print("\n=== A2. AGENT MARKET SHARE (% of agent PRs, by month) ===")
print(share.round(1).to_string())

# B. MERGE RATE ---------------------------------------------------------
mrate = monthly_table("is_merged", "mean") * 100
print("\n=== B. MONTHLY MERGE RATE (%) ===")
print(mrate.round(1).to_string())

# C. TIME TO MERGE (median hours, merged only) --------------------------
ttm = df[df["is_merged"]].pivot_table(index="month", columns="group", values="hours_to_merge", aggfunc="median").sort_index()
print("\n=== C. MONTHLY MEDIAN TIME-TO-MERGE (hours, merged PRs) ===")
print(ttm.round(2).to_string())

# D. DESCRIPTION LENGTH (median words) ----------------------------------
desc = monthly_table("body_words", "median")
print("\n=== D. MONTHLY MEDIAN PR DESCRIPTION (words) ===")
print(desc.round(0).to_string())

# Save tables as CSVs
from pathlib import Path
outdir = Path("results/temporal_figures"); outdir.mkdir(parents=True, exist_ok=True)
vol.to_csv("results/temporal_volume.csv")
share.round(2).to_csv("results/temporal_agent_share.csv")
mrate.round(2).to_csv("results/temporal_merge_rate.csv")
ttm.round(3).to_csv("results/temporal_time_to_merge.csv")
desc.round(1).to_csv("results/temporal_desc_words.csv")

# Figures
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
COLORS = {"Copilot":"#1f77b4","Cursor":"#2ca02c","Claude_Code":"#ff7f0e","Devin":"#d62728","Human":"#7f7f7f"}
order = ["Claude_Code","Cursor","Copilot","Devin","Human"]

def lineplot(tbl, title, ylabel, fname, logy=False, cols=None):
    fig, ax = plt.subplots(figsize=(11,5))
    x = range(len(tbl.index))
    for g in (cols or order):
        if g in tbl.columns:
            ax.plot(x, tbl[g].values, marker="o", ms=4, label=g.replace("_"," "), color=COLORS.get(g))
    ax.set_xticks(list(x)); ax.set_xticklabels(tbl.index, rotation=45, ha="right")
    ax.set_title(title); ax.set_ylabel(ylabel); ax.legend(ncol=5, fontsize=9)
    if logy: ax.set_yscale("log")
    ax.grid(True, alpha=0.3)
    fig.tight_layout(); fig.savefig(outdir/fname, dpi=150, bbox_inches="tight"); plt.close(fig)
    print("  ->", outdir/fname)

# Stacked area for market share
fig, ax = plt.subplots(figsize=(11,5))
ax.stackplot(range(len(share.index)), *[share[a].values for a in ["Claude_Code","Cursor","Copilot","Devin"]],
             labels=["Claude Code","Cursor","Copilot","Devin"],
             colors=[COLORS["Claude_Code"],COLORS["Cursor"],COLORS["Copilot"],COLORS["Devin"]])
ax.set_xticks(range(len(share.index))); ax.set_xticklabels(share.index, rotation=45, ha="right")
ax.set_title("Agent market share among agent fix-PRs (%)"); ax.set_ylabel("% of agent PRs"); ax.set_ylim(0,100)
ax.legend(loc="upper left", ncol=4, fontsize=9)
fig.tight_layout(); fig.savefig(outdir/"agent_market_share.png", dpi=150, bbox_inches="tight"); plt.close(fig)
print("  ->", outdir/"agent_market_share.png")

lineplot(vol, "Monthly fix-PR volume by group", "PRs", "volume.png")
lineplot(mrate, "Monthly merge rate by group (%)", "merge rate %", "merge_rate.png")
lineplot(ttm, "Monthly median time-to-merge (hours, merged PRs)", "hours (log)", "time_to_merge.png", logy=True)
lineplot(desc, "Monthly median PR description length (words)", "words", "desc_words.png")
print("\nDone. CSVs in results/, figures in", outdir)
