"""
Compare 3 PR datasets and produce:
  1. dataset_comparison.csv   — per-repo breakdown
  2. status_breakdown.csv     — status summary
  3. monthly_comparison.csv   — monthly trends
  4. Sends all files + summary to Telegram

Overlap is matched on (repo_name, PR number) since AIDev uses GraphQL node IDs
while our collection uses REST API IDs.
"""
import pandas as pd
import json
import os
import requests
from huggingface_hub import hf_hub_download
from dotenv import load_dotenv

load_dotenv()

OUR_AGENTS = {"Claude_Code", "Cursor", "Copilot", "Devin"}

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


def send_telegram(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def send_telegram_file(path: str, caption: str = ""):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        with open(path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument",
                data={"chat_id": TG_CHAT, "caption": caption},
                files={"document": (os.path.basename(path), f)},
                timeout=30,
            )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# 1. Load datasets
# ─────────────────────────────────────────────────────────────────────
print("Loading AIDev dataset...")
aidev_path = hf_hub_download(
    repo_id="hao-li/AIDev",
    filename="all_pull_request.parquet",
    repo_type="dataset",
)
aidev = pd.read_parquet(aidev_path, columns=["id", "number", "created_at", "agent", "repo_url"])
aidev["repo_name"] = aidev["repo_url"].str.replace("https://api.github.com/repos/", "", regex=False)
aidev["created_at"] = pd.to_datetime(aidev["created_at"], errors="coerce")
aidev["match_key"] = aidev["repo_name"] + "#" + aidev["number"].astype(str)

print("Loading previous collection (mabujadallah)...")
prev_path = hf_hub_download(
    repo_id="mabujadallah/GitHub-Agentic-PR-Dataset",
    filename="all_pull_requests.parquet",
    repo_type="dataset",
)
prev = pd.read_parquet(prev_path, columns=["id", "number", "created_at", "repo_name", "agent"])
prev["created_at"] = pd.to_datetime(prev["created_at"], errors="coerce")
prev["match_key"] = prev["repo_name"] + "#" + prev["number"].astype(str)

print("Loading new incremental collection...")
new = pd.read_parquet(
    "data_incremental/agent_pull_requests_incremental.parquet",
    columns=["id", "number", "created_at", "repo_name", "agent"],
)
new["created_at"] = pd.to_datetime(new["created_at"], errors="coerce")
new["match_key"] = new["repo_name"] + "#" + new["number"].astype(str)

# Combined (exclude human PRs from prev, dedup by match_key)
ours = pd.concat([prev[prev["agent"] != "human"], new], ignore_index=True)
ours = ours.drop_duplicates(subset=["match_key"], keep="first")
our_keys = set(ours["match_key"])

# ─────────────────────────────────────────────────────────────────────
# 2. Load checkpoint data for failure reasons
# ─────────────────────────────────────────────────────────────────────
with open("data_incremental/checkpoint_missing.json") as f:
    ckpt = json.load(f)

error_repos = set(ckpt.get("errors", []))
not_found_repos = set(ckpt.get("not_found", []))
done_repos = set(ckpt.get("done_repos", []))

# ─────────────────────────────────────────────────────────────────────
# 3. Per-repo comparison (dataset_comparison.csv)
# ─────────────────────────────────────────────────────────────────────
print("Building per-repo comparison...")

# Tag overlap on AIDev
aidev["in_ours"] = aidev["match_key"].isin(our_keys)

# Aggregations
aidev_total = aidev.groupby("repo_name")["id"].nunique().rename("aidev_total_prs")
aidev_target = (
    aidev[aidev["agent"].isin(OUR_AGENTS)]
    .groupby("repo_name")["id"]
    .nunique()
    .rename("aidev_target_agent_prs")
)
aidev_by_agent = aidev.groupby(["repo_name", "agent"])["id"].nunique().unstack(fill_value=0)
aidev_by_agent.columns = [f"aidev_{c}_prs" for c in aidev_by_agent.columns]

overlap_per_repo = aidev.groupby("repo_name")["in_ours"].sum().astype(int).rename("overlap_count")

prev_total = (
    prev[prev["agent"] != "human"]
    .groupby("repo_name")["id"]
    .nunique()
    .rename("prev_collected_prs")
)
new_total = new.groupby("repo_name")["id"].nunique().rename("new_collected_prs")

# Assemble
df = pd.DataFrame({"repo_name": aidev_total.index})
for series in [aidev_total, aidev_target, aidev_by_agent, overlap_per_repo, prev_total, new_total]:
    df = df.merge(series, left_on="repo_name", right_index=True, how="left")

# Fill NaNs
for c in df.columns:
    if c != "repo_name" and c != "aidev_total_prs":
        df[c] = df[c].fillna(0).astype(int)

df["our_total_collected"] = df["prev_collected_prs"] + df["new_collected_prs"]
df["overlap_pct"] = (df["overlap_count"] / df["aidev_total_prs"] * 100).round(1)
df["pct_of_target_collected"] = (
    df["our_total_collected"] / df["aidev_target_agent_prs"].replace(0, float("nan")) * 100
).round(1)
df["pct_of_all_collected"] = (df["our_total_collected"] / df["aidev_total_prs"] * 100).round(1)


def get_reason(row):
    repo = row["repo_name"]
    target = row["aidev_target_agent_prs"]
    collected = row["our_total_collected"]

    if target > 0 and collected >= target:
        return "fully_collected"
    if repo in error_repos:
        return "error_accessing_repo"
    if repo in not_found_repos:
        return "repo_not_found_or_deleted"
    if target == 0:
        return "no_target_agent_prs (only OpenAI_Codex or other)"
    if repo in done_repos and collected > 0:
        return "partially_collected"
    if repo in done_repos and collected == 0:
        return "processed_but_no_matching_prs_found"
    if repo not in done_repos and repo not in not_found_repos and repo not in error_repos:
        return "not_attempted (repo not in our collection scope)"
    return "unknown"


df["status"] = df.apply(get_reason, axis=1)
df = df.sort_values("aidev_total_prs", ascending=False)
df.to_csv("dataset_comparison.csv", index=False)
print(f"Saved dataset_comparison.csv ({len(df)} repos)")

# ─────────────────────────────────────────────────────────────────────
# 4. Status breakdown (status_breakdown.csv)
# ─────────────────────────────────────────────────────────────────────
summary = df["status"].value_counts().reset_index()
summary.columns = ["status", "repo_count"]
summary["percentage"] = (summary["repo_count"] / summary["repo_count"].sum() * 100).round(2)
summary.to_csv("status_breakdown.csv", index=False)
print(f"Saved status_breakdown.csv")

# ─────────────────────────────────────────────────────────────────────
# 5. Monthly comparison (monthly_comparison.csv)
# ─────────────────────────────────────────────────────────────────────
print("Building monthly comparison...")

aidev["month"] = aidev["created_at"].dt.to_period("M").astype(str)
ours["month"] = ours["created_at"].dt.to_period("M").astype(str)
aidev["is_our_agent"] = aidev["agent"].isin(OUR_AGENTS)
aidev["is_inaccessible"] = aidev["repo_name"].isin(error_repos | not_found_repos)

all_months = sorted(set(aidev["month"].dropna()) | set(ours["month"].dropna()))

rows = []
for month in all_months:
    ai_m = aidev[aidev["month"] == month]
    our_m = ours[ours["month"] == month]

    ai_keys_m = set(ai_m["match_key"])
    our_keys_m = set(our_m["match_key"])
    overlap = len(ai_keys_m & our_keys_m)
    our_unique = len(our_keys_m - ai_keys_m)

    ai_target_keys = set(ai_m[ai_m["is_our_agent"]]["match_key"])
    missing_target = len(ai_target_keys - our_keys_m)

    ai_by = ai_m["agent"].value_counts()
    our_by = our_m["agent"].value_counts()

    rows.append({
        "month": month,
        "aidev_prs": len(ai_m),
        "aidev_target_agent_prs": int(ai_m["is_our_agent"].sum()),
        "aidev_out_of_scope_prs": int((~ai_m["is_our_agent"]).sum()),
        "aidev_inaccessible_repo_prs": int(ai_m["is_inaccessible"].sum()),
        "our_prs": len(our_m),
        "overlap_prs": overlap,
        "our_unique_prs": our_unique,
        "aidev_target_we_miss": missing_target,
        "aidev_OpenAI_Codex": int(ai_by.get("OpenAI_Codex", 0)),
        "aidev_Claude_Code": int(ai_by.get("Claude_Code", 0)),
        "aidev_Cursor": int(ai_by.get("Cursor", 0)),
        "aidev_Copilot": int(ai_by.get("Copilot", 0)),
        "aidev_Devin": int(ai_by.get("Devin", 0)),
        "our_Claude_Code": int(our_by.get("Claude_Code", 0)),
        "our_Cursor": int(our_by.get("Cursor", 0)),
        "our_Copilot": int(our_by.get("Copilot", 0)),
        "our_Devin": int(our_by.get("Devin", 0)),
    })

report = pd.DataFrame(rows)
totals = report.select_dtypes(include="number").sum()
totals["month"] = "TOTAL"
report = pd.concat([report, pd.DataFrame([totals])], ignore_index=True)
report.to_csv("monthly_comparison.csv", index=False)
print(f"Saved monthly_comparison.csv ({len(report)} rows)")

# ─────────────────────────────────────────────────────────────────────
# 6. Per-agent overlap summary
# ─────────────────────────────────────────────────────────────────────
agent_overlap = {}
for agent in ["OpenAI_Codex"] + sorted(OUR_AGENTS):
    a = aidev[aidev["agent"] == agent]
    matched = int(a["in_ours"].sum())
    agent_overlap[agent] = {"total": len(a), "overlap": matched}

# ─────────────────────────────────────────────────────────────────────
# 7. Print & send to Telegram
# ─────────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
print(f"AIDev PRs:       {len(aidev):,}")
print(f"Our PRs:         {len(ours):,}")
print(f"Overlap:         {len(our_keys & set(aidev['match_key'])):,}")
print(f"\nStatus breakdown:")
print(summary.to_string(index=False))
print(f"\nPer-agent overlap:")
for agent, v in agent_overlap.items():
    pct = v["overlap"] / max(v["total"], 1) * 100
    print(f"  {agent}: {v['overlap']:,} / {v['total']:,} ({pct:.1f}%)")

# Telegram summary
overlap_total = len(our_keys & set(aidev["match_key"]))
msg_lines = [
    "<b>Dataset Overlap Summary</b>",
    f"(matched on repo_name + PR number)",
    "",
    f"<b>Real overlap: {overlap_total:,} PRs</b>",
    "",
    "<b>Per-agent coverage of AIDev PRs:</b>",
]
for agent in sorted(OUR_AGENTS):
    v = agent_overlap[agent]
    pct = v["overlap"] / max(v["total"], 1) * 100
    msg_lines.append(f"  {agent}: {v['overlap']:,} / {v['total']:,} ({pct:.1f}%)")
v = agent_overlap["OpenAI_Codex"]
msg_lines.append(f"  OpenAI_Codex: {v['overlap']:,} / {v['total']:,} ({v['overlap']/max(v['total'],1)*100:.1f}%) — not our scope")
msg_lines += [
    "",
    "<b>Totals:</b>",
    f"  AIDev PRs:     {len(aidev):,}",
    f"  Our PRs:       {len(ours):,}",
    f"  Overlap:       {overlap_total:,}",
    f"  AIDev-only:    {len(set(aidev['match_key']) - our_keys):,} (mostly OpenAI_Codex)",
    f"  Ours-only:     {len(our_keys - set(aidev['match_key'])):,} (Aug 2025 → Feb 2026)",
    f"  Missing target: {int(report[report['month']=='TOTAL']['aidev_target_we_miss'].iloc[0]):,} (inaccessible/deleted repos)",
]

send_telegram("\n".join(msg_lines))

for fp, cap in [
    ("dataset_comparison.csv", "Per-repo comparison"),
    ("status_breakdown.csv", "Status breakdown"),
    ("monthly_comparison.csv", "Monthly comparison"),
]:
    send_telegram_file(fp, cap)

print("\nAll files saved and sent to Telegram.")