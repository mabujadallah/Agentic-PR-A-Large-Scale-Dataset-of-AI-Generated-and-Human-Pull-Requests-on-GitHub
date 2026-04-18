import pandas as pd
import numpy as np

print("=" * 80)
print("INVESTIGATING ACCEPTANCE RATE GAP: AIDev (53%) vs Own Data (84%)")
print("=" * 80)

# ── Load AIDev data (local files) ──
print("\n1. Loading AIDev data...")
pr_df = pd.read_parquet("pull_request.parquet")
pr_task = pd.read_parquet("pr_task_type.parquet")

fix_prs = pr_task[pr_task["type"] == "fix"]
fix_clean = fix_prs.drop(columns=["title", "agent"], errors="ignore")
aidev = pd.merge(pr_df, fix_clean, on="id", how="inner")
aidev = aidev[aidev["state"] == "closed"]
aidev = aidev[aidev["agent"] != "OpenAI_Codex"]
aidev["accepted"] = aidev["merged_at"].notna()

print(f"   AIDev fix PRs (closed, no Codex): {len(aidev):,}")
print(f"   Acceptance rate: {aidev['accepted'].mean()*100:.2f}%")

# ── Load own data (local file) ──
print("\n2. Loading own data (local)...")
own = pd.read_parquet("fix_classified_prs.parquet",
                      columns=["type", "source", "state", "agent", "merged_at", "repo_name", "number", "created_at"])
own = own[
    (own["type"] == "fix") &
    (own["state"] == "closed") &
    (own["source"] == "agent") &
    (own["agent"] != "OpenAI_Codex") &
    (own["agent"] != "human")
]
own["accepted"] = own["merged_at"].notna()

print(f"   Own fix PRs (closed, agent, no Codex/human): {len(own):,}")
print(f"   Acceptance rate: {own['accepted'].mean()*100:.2f}%")

# ── Compare agent distribution ──
print("\n" + "=" * 80)
print("3. AGENT DISTRIBUTION COMPARISON")
print("=" * 80)
aidev_agents = aidev.groupby("agent").agg(
    total=("accepted", "count"),
    merged=("accepted", "sum"),
).assign(rate=lambda x: x["merged"]/x["total"]*100)

own_agents = own.groupby("agent").agg(
    total=("accepted", "count"),
    merged=("accepted", "sum"),
).assign(rate=lambda x: x["merged"]/x["total"]*100)

print("\nAIDev per-agent:")
for ag, row in aidev_agents.iterrows():
    print(f"  {ag:<20} total={int(row['total']):>6,}  merged={int(row['merged']):>6,}  rate={row['rate']:.1f}%")

print("\nOwn data per-agent:")
for ag, row in own_agents.iterrows():
    print(f"  {ag:<20} total={int(row['total']):>6,}  merged={int(row['merged']):>6,}  rate={row['rate']:.1f}%")

# ── Compare: count proportions ──
print("\n" + "=" * 80)
print("4. AGENT PROPORTION (% of total PRs)")
print("=" * 80)
aidev_pct = (aidev_agents["total"] / aidev_agents["total"].sum() * 100).round(1)
own_pct = (own_agents["total"] / own_agents["total"].sum() * 100).round(1)

combined = pd.DataFrame({
    "AIDev %": aidev_pct,
    "Own %": own_pct,
    "AIDev rate": aidev_agents["rate"].round(1),
    "Own rate": own_agents["rate"].round(1),
}).fillna(0)
print(combined.to_string())

# ── Check time ranges ──
print("\n" + "=" * 80)
print("5. TIME RANGE COMPARISON")
print("=" * 80)
aidev["created_at"] = pd.to_datetime(aidev["created_at"], errors="coerce")
own["created_at"] = pd.to_datetime(own["created_at"], errors="coerce")
print(f"AIDev:    {aidev['created_at'].min()} → {aidev['created_at'].max()}")
print(f"Own data: {own['created_at'].min()} → {own['created_at'].max()}")

# ── Check if own data has extra months beyond AIDev ──
aidev_max = aidev["created_at"].max()
own_beyond = own[own["created_at"] > aidev_max]
own_within = own[own["created_at"] <= aidev_max]

print(f"\nOwn PRs within AIDev time range: {len(own_within):,} (rate: {own_within['accepted'].mean()*100:.2f}%)")
print(f"Own PRs BEYOND AIDev time range: {len(own_beyond):,} (rate: {own_beyond['accepted'].mean()*100:.2f}%)")

# ── Check repo overlap ──
print("\n" + "=" * 80)
print("6. REPOSITORY OVERLAP")
print("=" * 80)
if "repo_name" not in aidev.columns and "repo_url" in aidev.columns:
    aidev["repo_name"] = aidev["repo_url"].str.replace("https://api.github.com/repos/", "", regex=False)

aidev_repos = set(aidev["repo_name"].unique())
own_repos = set(own["repo_name"].unique())
shared = aidev_repos & own_repos
print(f"AIDev repos: {len(aidev_repos):,}")
print(f"Own repos:   {len(own_repos):,}")
print(f"Shared:      {len(shared):,}")
print(f"Only AIDev:  {len(aidev_repos - own_repos):,}")
print(f"Only own:    {len(own_repos - aidev_repos):,}")

# Rate in shared repos only
aidev_shared = aidev[aidev["repo_name"].isin(shared)]
own_shared = own[own["repo_name"].isin(shared)]
print(f"\nIn shared repos only:")
print(f"  AIDev: {len(aidev_shared):,} PRs, rate={aidev_shared['accepted'].mean()*100:.2f}%")
print(f"  Own:   {len(own_shared):,} PRs, rate={own_shared['accepted'].mean()*100:.2f}%")

# ── Overlapping PRs — same match key ──
print("\n" + "=" * 80)
print("7. SAME PRs (overlapping match keys)")
print("=" * 80)
aidev["match_key"] = aidev["repo_name"] + "#" + aidev["number"].astype(str)
own["match_key"] = own["repo_name"] + "#" + own["number"].astype(str)

overlap_keys = set(aidev["match_key"]) & set(own["match_key"])
print(f"Overlapping PRs: {len(overlap_keys):,}")

aidev_overlap = aidev[aidev["match_key"].isin(overlap_keys)]
own_overlap = own[own["match_key"].isin(overlap_keys)]
print(f"  AIDev overlap rate: {aidev_overlap['accepted'].mean()*100:.2f}%")
print(f"  Own overlap rate:   {own_overlap['accepted'].mean()*100:.2f}%")

# PRs only in own data
own_only_keys = set(own["match_key"]) - set(aidev["match_key"])
own_exclusive = own[own["match_key"].isin(own_only_keys)]
print(f"\nPRs only in own data: {len(own_exclusive):,}")
if len(own_exclusive) > 0:
    print(f"  Rate: {own_exclusive['accepted'].mean()*100:.2f}%")
    print(f"  Agent distribution:")
    for ag, cnt in own_exclusive["agent"].value_counts().items():
        sub = own_exclusive[own_exclusive["agent"] == ag]
        print(f"    {ag:<20} {cnt:>6,}  rate={sub['accepted'].mean()*100:.1f}%")

# ── Check for classification differences ──
print("\n" + "=" * 80)
print("8. CLASSIFICATION DIFFERENCES ON SAME PRs")
print("=" * 80)
aidev_ov = aidev[aidev["match_key"].isin(overlap_keys)][["match_key", "accepted", "agent"]].drop_duplicates("match_key")
own_ov = own[own["match_key"].isin(overlap_keys)][["match_key", "accepted", "agent"]].drop_duplicates("match_key")

merged = aidev_ov.merge(own_ov, on="match_key", suffixes=("_aidev", "_own"))
agree = (merged["accepted_aidev"] == merged["accepted_own"]).sum()
disagree = len(merged) - agree
print(f"Same accepted status: {agree:,} ({agree/len(merged)*100:.1f}%)")
print(f"Different status:     {disagree:,} ({disagree/len(merged)*100:.1f}%)")

if disagree > 0:
    diff = merged[merged["accepted_aidev"] != merged["accepted_own"]]
    aidev_yes_own_no = (diff["accepted_aidev"] & ~diff["accepted_own"]).sum()
    aidev_no_own_yes = (~diff["accepted_aidev"] & diff["accepted_own"]).sum()
    print(f"  AIDev=merged, Own=not merged: {aidev_yes_own_no:,}")
    print(f"  AIDev=not merged, Own=merged: {aidev_no_own_yes:,}")

print("\n" + "=" * 80)
print("CONCLUSION")
print("=" * 80)
