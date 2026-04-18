import pandas as pd

own_url = "https://huggingface.co/datasets/mabujadallah/GitHub-Agentic-PR-Dataset/resolve/main/fix_classified_prs.parquet"
own = pd.read_parquet(own_url, columns=["type","source","state","agent","merged_at"])

print("=== OWN DATA (fix_classified_prs.parquet) ===")
print(f"Total rows: {len(own):,}")
print(f"type distribution:\n{own['type'].value_counts()}")
print(f"\nsource distribution:\n{own['source'].value_counts()}")
print(f"\nagent distribution:\n{own['agent'].value_counts()}")
print(f"\nstate distribution:\n{own['state'].value_counts()}")

filt = own[
    (own["type"]=="fix") & 
    (own["state"]=="closed") & 
    (own["source"]=="agent") & 
    (own["agent"]!="OpenAI_Codex") & 
    (own["agent"]!="human")
]
n = len(filt)
m = filt["merged_at"].notna().sum()
print(f"\nAfter filtering: {n:,} rows, merged: {m:,}, rate: {m/n*100:.2f}%")

# Per-agent breakdown
for agent in sorted(filt["agent"].unique()):
    sub = filt[filt["agent"]==agent]
    sm = sub["merged_at"].notna().sum()
    print(f"  {agent}: {len(sub):,} total, {sm:,} merged, rate={sm/len(sub)*100:.2f}%")
