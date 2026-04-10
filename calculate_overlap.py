import pandas as pd
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

def send_telegram(msg: str):
    """Send a message via Telegram Bot API."""
    if not _TG_TOKEN or not _TG_CHAT:
        print("Telegram credentials not found. Skipping Telegram report.")
        print(msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        print("Successfully sent report to Telegram.")
    except Exception as e:
        print(f"Failed to send to Telegram: {e}")

def send_telegram_document(file_path: str, caption: str = ""):
    """Send a file via Telegram Bot API."""
    if not _TG_TOKEN or not _TG_CHAT:
        return
    try:
        with open(file_path, 'rb') as f:
            requests.post(
                f"https://api.telegram.org/bot{_TG_TOKEN}/sendDocument",
                data={"chat_id": _TG_CHAT, "caption": caption},
                files={"document": f},
                timeout=30,
            )
        print(f"Successfully sent {file_path} to Telegram.")
    except Exception as e:
        print(f"Failed to send document: {e}")

def main():
    print("Loading local data from parquet...")
    
    local_pr_path = "data_dec2024_feb2026/all_pull_requests.parquet"
    local_commits_path = "data_dec2024_feb2026/pr_commits.parquet"
    
    if not os.path.exists(local_pr_path) or not os.path.exists(local_commits_path):
        print("Error: Local parquet files not found. Please run the collector first.")
        return

    # 1. Load Local PRs (ALL)
    local_all_df = pd.read_parquet(
        local_pr_path,
        columns=["repo_id", "number", "id", "created_at", "is_agent"]
    )
    # Standardize types
    local_all_df['repo_id'] = pd.to_numeric(local_all_df['repo_id'], errors='coerce').fillna(0).astype('int64')
    local_all_df['number'] = pd.to_numeric(local_all_df['number'], errors='coerce').fillna(0).astype('int64')
    local_all_df['id'] = pd.to_numeric(local_all_df['id'], errors='coerce').fillna(0).astype('int64')
    local_all_df['created_at'] = pd.to_datetime(local_all_df['created_at'])
    local_all_df['month'] = local_all_df['created_at'].dt.to_period('M')
    
    local_agent_df = local_all_df[local_all_df['is_agent'] == True]
    print(f"Loaded {len(local_all_df):,} Total Local PRs ({len(local_agent_df):,} Agent PRs).")

    # 2. Load Local Commits
    local_commits_df = pd.read_parquet(local_commits_path, columns=["pr_id", "sha"])
    local_commits_df['pr_id'] = pd.to_numeric(local_commits_df['pr_id'], errors='coerce').fillna(0).astype('int64')
    local_all_shas = set(local_commits_df['sha'].dropna())
    print(f"Loaded {len(local_all_shas):,} unique local SHAs.")

    print("\nLoading AIDev dataset from Hugging Face...")
    # 3. Load AIDev PRs (ALL)
    aidev_df = pd.read_parquet(
        "hf://datasets/hao-li/AIDev/all_pull_request.parquet",
        columns=['repo_id', 'number', 'id', 'created_at', 'agent']
    )
    # Standardize types
    aidev_df['repo_id'] = pd.to_numeric(aidev_df['repo_id'], errors='coerce').fillna(0).astype('int64')
    aidev_df['number'] = pd.to_numeric(aidev_df['number'], errors='coerce').fillna(0).astype('int64')
    aidev_df['id'] = pd.to_numeric(aidev_df['id'], errors='coerce').fillna(0).astype('int64')
    aidev_df['created_at'] = pd.to_datetime(aidev_df['created_at'])
    aidev_df['month'] = aidev_df['created_at'].dt.to_period('M')
    
    print(f"Loaded {len(aidev_df):,} AIDev PRs.")

    # 4. Load AIDev Commits
    aidev_commits_df = pd.read_parquet("hf://datasets/hao-li/AIDev/pr_commits.parquet", columns=["pr_id", "sha"])
    aidev_commits_df['pr_id'] = pd.to_numeric(aidev_commits_df['pr_id'], errors='coerce').fillna(0).astype('int64')
    aidev_shas_to_pr = aidev_commits_df.groupby('sha')['pr_id'].apply(set).to_dict()
    aidev_all_shas = set(aidev_shas_to_pr.keys())

    # --- Overlap Calculation ---

    # Metadata matching (All)
    local_meta_keys = set(zip(local_all_df['repo_id'], local_all_df['number']))
    aidev_meta_keys = set(zip(aidev_df['repo_id'], aidev_df['number']))
    meta_overlap_keys = local_meta_keys & aidev_meta_keys
    
    # SHA matching (All)
    sha_overlap = local_all_shas & aidev_all_shas
    
    # Map back to AIDev PR IDs
    matched_aidev_ids_by_sha = set()
    for sha in sha_overlap:
        matched_aidev_ids_by_sha.update(aidev_shas_to_pr.get(sha, set()))

    matched_aidev_ids_by_meta = set(aidev_df[
        aidev_df.set_index(['repo_id', 'number']).index.isin(meta_overlap_keys)
    ]['id'])

    unified_matched_aidev_ids = matched_aidev_ids_by_meta | matched_aidev_ids_by_sha
    
    total_aidev = len(aidev_df)
    
    report_lines = [
        "<b>📊 PR Collection vs AIDev Overlap Report (V4)</b>",
        f"<b>Local Total PRs:</b>  {len(local_all_df):,}",
        f"<b>Local Agent PRs:</b>  {len(local_agent_df):,}",
        f"<b>AIDev Agent PRs:</b>  {total_aidev:,}",
        "",
        "<b>Overlap Metrics (Unified):</b>",
        f"• Total Overlap:        {len(unified_matched_aidev_ids):,} ({len(unified_matched_aidev_ids)/total_aidev*100:.1f}%)",
        "",
        "<b>📅 Monthly Breakdown (Unified Match):</b>",
        "<pre>",
        f"{'Month':<10} | {'LocalAll':<8} | {'AIDev':<7} | {'Overlap':<7} | {'% AIDev':<7}",
        "-" * 55
    ]
    
    months = sorted(list(set(local_all_df['month'].unique()) | set(aidev_df['month'].unique())))
    
    for month in months:
        m_local_count = len(local_all_df[local_all_df['month'] == month])
        m_aidev_subset = aidev_df[aidev_df['month'] == month]
        m_aidev_count = len(m_aidev_subset)
        
        if m_aidev_count == 0 and m_local_count == 0:
            continue
            
        m_aidev_ids = set(m_aidev_subset['id'])
        m_overlap_count = len(m_aidev_ids & unified_matched_aidev_ids)
        
        pct = (m_overlap_count / m_aidev_count * 100) if m_aidev_count > 0 else 0
        
        report_lines.append(
            f"{str(month):<10} | {m_local_count:<8} | {m_aidev_count:<7} | {m_overlap_count:<7} | {pct:>5.1f}%"
        )
        
    report_lines.append("</pre>")
    
    # 5. Per-Repo Deep Dive (Top 20 repos in 2025-07)
    DEEP_DIVE_MONTH = '2025-07'
    print(f"\nPerforming deep dive for {DEEP_DIVE_MONTH}...")
    
    m_aidev_deep = aidev_df[aidev_df['month'] == DEEP_DIVE_MONTH].copy()
    if not m_aidev_deep.empty:
        # Load repo metadata to get names
        repo_meta_df = pd.read_parquet("hf://datasets/hao-li/AIDev/repository.parquet", columns=['id', 'full_name'])
        repo_meta_df['id'] = pd.to_numeric(repo_meta_df['id'], errors='coerce').fillna(0).astype('int64')
        repo_id_to_name = repo_meta_df.set_index('id')['full_name'].to_dict()
        
        m_aidev_deep['is_overlap'] = m_aidev_deep['id'].isin(unified_matched_aidev_ids)
        
        repo_stats = m_aidev_deep.groupby('repo_id').agg(
            aidev_total=('id', 'count'),
            overlap_count=('is_overlap', 'sum')
        ).reset_index()
        
        repo_stats['repo_name'] = repo_stats['repo_id'].map(repo_id_to_name).fillna("Unknown")
        
        # Debug Unknowns
        unknown_repo_ids = repo_stats[repo_stats['repo_name'] == "Unknown"]['repo_id'].head(5).tolist()
        if unknown_repo_ids:
            print(f"DEBUG: Sample Unknown Repo IDs: {unknown_repo_ids}")
            
        repo_stats['overlap_pct'] = (repo_stats['overlap_count'] / repo_stats['aidev_total'] * 100).round(1)
        repo_stats_sorted = repo_stats.sort_values(by='aidev_total', ascending=False)
        
        # Save to CSV
        csv_filename = f"repo_overlap_{DEEP_DIVE_MONTH.replace('-', '_')}.csv"
        repo_stats_sorted.to_csv(csv_filename, index=False)
        print(f"Saved deep dive to {csv_filename}")

        top_20 = repo_stats_sorted.head(20)
        report_lines.append(f"\n<b>🔍 Deep Dive: Top Repos in {DEEP_DIVE_MONTH}</b>")
        report_lines.append("<pre>")
        report_lines.append(f"{'Repository':<30} | {'AIDev':<7} | {'Overlap':<7} | {'%':<5}")
        report_lines.append("-" * 55)
        for _, row in top_20.iterrows():
            name = row['repo_name']
            if len(name) > 30: name = name[:27] + "..."
            report_lines.append(f"{name:<30} | {int(row['aidev_total']):<7} | {int(row['overlap_count']):<7} | {row['overlap_pct']}%")
        report_lines.append("</pre>")
        
        # Send CSV to Telegram
        send_telegram_document(csv_filename, f"Detailed overlap stats per repository for {DEEP_DIVE_MONTH}")
        
        # 6. Commit-to-Commit (SHA) Overlap CSV for the same month
        m_aidev_commits = aidev_commits_df[aidev_commits_df['pr_id'].isin(m_aidev_ids)]
        m_aidev_shas = set(m_aidev_commits['sha'])
        m_sha_overlap = m_aidev_shas & local_all_shas
        
        if m_sha_overlap:
            sha_data = []
            for sha in m_sha_overlap:
                pr_ids = aidev_shas_to_pr.get(sha, set())
                for pid in pr_ids:
                    if pid in m_aidev_ids: # only current month
                        sha_data.append({'sha': sha, 'aidev_pr_id': pid})
            
            sha_df = pd.DataFrame(sha_data)
            sha_csv = f"sha_overlap_{DEEP_DIVE_MONTH.replace('-', '_')}.csv"
            sha_df.to_csv(sha_csv, index=False)
            print(f"Saved SHA overlap to {sha_csv} ({len(sha_df)} SHAs)")
            send_telegram_document(sha_csv, f"Commit-to-commit SHA overlap for {DEEP_DIVE_MONTH}")
    
    report_text = "\n".join(report_lines)
    
    with open("overlap_report.txt", "w", encoding="utf-8") as f:
        clean_text = report_text.replace("<b>", "").replace("</b>", "").replace("<pre>", "").replace("</pre>", "")
        f.write(clean_text)
        
    print("\n" + report_text.replace("<b>", "").replace("</b>", "").replace("<pre>", "").replace("</pre>", ""))
    
    send_telegram(report_text)


if __name__ == "__main__":
    main()
