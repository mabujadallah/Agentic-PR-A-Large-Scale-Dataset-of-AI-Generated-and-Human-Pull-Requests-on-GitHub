"""
collect_missing.py — Incremental collector for missing agent PRs.

Compares our dataset (mabujadallah/GitHub-Agentic-PR-Dataset) with the AIDev
dataset (hao-li/AIDev) to find repos we haven't collected yet, prioritises
them by agent-PR count, and collects only agent PRs in memory-efficient
batches.  Results are written as incremental parquet files that can be merged
with the existing dataset later.

Usage:
    python collect_missing.py
"""

import pandas as pd
import requests
import time
import os
import json
import re
import subprocess
import shutil
import threading
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

# ════════════════════════════════════════════
#  Configuration
# ════════════════════════════════════════════

GITHUB_TOKENS = os.getenv("GITHUB_TOKENS", "").split(",")
GITHUB_TOKENS = [t.strip() for t in GITHUB_TOKENS if t.strip()]

if not GITHUB_TOKENS:
    raise RuntimeError("GITHUB_TOKENS is empty — set comma-separated tokens in .env")

NUM_WORKERS = len(GITHUB_TOKENS) * 8
AGENTS_TO_KEEP = ['Devin', 'Copilot', 'Cursor', 'Claude_Code']
DATE_FROM = "2024-12-01"
DATE_TO   = "2026-02-28"

# Output
OUTPUT_DIR = "data_incremental"
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "checkpoint_missing.json")
BATCH_SIZE = 50  # repos per batch before flushing to parquet

# ── Phase 3: Local-git commit details config ──
REPO_CACHE_DIR    = "repo_cache"
DETAILS_CHUNK_DIR = os.path.join(OUTPUT_DIR, "detail_chunks")
CLONE_WORKERS     = 10
INCLUDE_PATCH     = True
MAX_PATCH_BYTES   = 1_000_000    # 1 MB
CLONE_TIMEOUT     = 3600
FETCH_TIMEOUT     = 600
EXTRACT_TIMEOUT   = 3600
SHA_BATCH_SIZE    = 500
DELETE_AFTER_EXTRACT = True

os.makedirs(OUTPUT_DIR, exist_ok=True)

print(f"Loaded {len(GITHUB_TOKENS)} tokens — {NUM_WORKERS} workers")

# Telegram
_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")


def send_telegram(msg: str):
    if not _TG_TOKEN or not _TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


# ════════════════════════════════════════════
#  .env hot-reload
# ════════════════════════════════════════════

try:
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
except NameError:
    _env_path = os.path.abspath(".env")
_env_mtime   = os.path.getmtime(_env_path) if os.path.exists(_env_path) else 0.0
_tokens_lock = threading.Lock()


def _load_tokens():
    return [t.strip() for t in os.getenv("GITHUB_TOKENS", "").split(",") if t.strip()]


def _env_watcher():
    global _env_mtime
    while True:
        time.sleep(30)
        try:
            if not os.path.exists(_env_path):
                continue
            mtime = os.path.getmtime(_env_path)
            if mtime != _env_mtime:
                load_dotenv(_env_path, override=True)
                new_tokens = _load_tokens()
                with _tokens_lock:
                    GITHUB_TOKENS[:] = new_tokens
                    _env_mtime = mtime
                print(f"  [env-watcher] Reloaded .env: {len(new_tokens)} tokens", flush=True)
                send_telegram(f"[env-watcher] Reloaded .env — {len(new_tokens)} tokens active.")
        except Exception as e:
            print(f"  [env-watcher] Error: {e}", flush=True)


threading.Thread(target=_env_watcher, daemon=True, name="env-watcher").start()

# ════════════════════════════════════════════
#  GitHub API helpers (session-pooled, token-rotating)
# ════════════════════════════════════════════

_thread_local = threading.local()
_NOT_FOUND = object()  # sentinel: repo deleted/private (don't retry)


def get_session(token_idx):
    if not hasattr(_thread_local, 'session'):
        _thread_local.session = requests.Session()
        _thread_local.session.headers["Accept"] = "application/vnd.github.v3+json"
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
        _thread_local.session.mount("https://", adapter)
    token = GITHUB_TOKENS[token_idx % len(GITHUB_TOKENS)]
    _thread_local.session.headers["Authorization"] = f"token {token}"
    _proxy = os.getenv("HTTPS_PROXY", "").strip()
    _thread_local.session.proxies = {"https": _proxy, "http": _proxy} if _proxy else {}
    return _thread_local.session


def github_get(url, params=None, token_idx=None):
    idx = token_idx if token_idx is not None else 0
    for attempt in range(len(GITHUB_TOKENS) * 2):
        session = get_session(idx)
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.exceptions.Timeout:
            print(f"  Timeout: {url}", flush=True)
            idx = (idx + 1) % len(GITHUB_TOKENS)
            continue
        except requests.exceptions.RequestException as e:
            msg = str(e).lower()
            transient = (
                'nameresolution' in msg or 'name resolution' in msg
                or 'failed to resolve' in msg or 'connection aborted' in msg
                or 'remotedisconnected' in msg or 'remote end closed' in msg
                or 'connection reset' in msg or 'ssleoferror' in msg
                or 'unexpected_eof' in msg or 'eof occurred' in msg
            )
            if transient:
                wait = min(30, 2 ** attempt)
                print(f"  Connection error (attempt {attempt}) — retrying in {wait}s: {e}", flush=True)
                time.sleep(wait)
                continue
            print(f"  Request error: {e}", flush=True)
            return None

        if r.status_code == 200:
            return r.json()

        if r.status_code == 403 and 'rate limit' in r.text.lower():
            remaining = int(r.headers.get('X-RateLimit-Remaining', 0))
            if remaining == 0:
                next_idx = (idx + 1) % len(GITHUB_TOKENS)
                if next_idx == (token_idx or 0) % len(GITHUB_TOKENS):
                    reset_time = int(r.headers.get('X-RateLimit-Reset', 0))
                    wait = max(reset_time - int(time.time()), 10)
                    print(f"  All tokens rate limited. Waiting {wait}s...", flush=True)
                    time.sleep(wait)
                idx = next_idx
            else:
                idx = (idx + 1) % len(GITHUB_TOKENS)
            continue

        if r.status_code == 429:
            retry_after = int(r.headers.get('Retry-After', 60))
            print(f"  Secondary rate limit (429) — sleeping {retry_after}s...", flush=True)
            time.sleep(retry_after)
            continue

        if r.status_code == 404:
            return _NOT_FOUND

        if r.status_code >= 500:
            wait = min(2 ** attempt, 30)
            print(f"  Server {r.status_code} on {url} — retrying in {wait}s...", flush=True)
            time.sleep(wait)
            idx = (idx + 1) % len(GITHUB_TOKENS)
            continue

        print(f"  Error {r.status_code}: {url}", flush=True)
        return None

    print(f"  Max retries exceeded: {url}", flush=True)
    return None


def github_get_all_pages(url, params=None, token_idx=None):
    if params is None:
        params = {}
    params = {**params, 'per_page': 100, 'page': 1}
    all_items = []
    while True:
        data = github_get(url, params, token_idx=token_idx)
        if data is None or data is _NOT_FOUND:
            return None
        if len(data) == 0:
            break
        all_items.extend(data)
        if len(data) < 100:
            break
        params = {**params, 'page': params['page'] + 1}
    return all_items


# ════════════════════════════════════════════
#  Heartbeat
# ════════════════════════════════════════════

_progress = {
    "phase": "startup",
    "repos_done": 0, "repos_total": 0,
    "prs_collected": 0,
    "batch": 0,
}


def _query_token_status() -> str:
    lines = []
    for i, token in enumerate(GITHUB_TOKENS):
        try:
            r = requests.get(
                "https://api.github.com/rate_limit",
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
                timeout=10,
            )
            if r.status_code == 200:
                d = r.json()["rate"]
                lines.append(f"  Token {i+1}: {d['remaining']}/{d['limit']}")
            else:
                lines.append(f"  Token {i+1}: HTTP {r.status_code}")
        except Exception as e:
            lines.append(f"  Token {i+1}: error ({e})")
    return "\n".join(lines)


def _heartbeat():
    while True:
        time.sleep(300)
        try:
            p = _progress
            msg = (
                f"<b>Missing-repo collector heartbeat</b>\n"
                f"Phase: {p['phase']}\n"
                f"Repos: {p['repos_done']}/{p['repos_total']}\n"
                f"Agent PRs collected: {p['prs_collected']:,}\n"
                f"Batch: {p['batch']}\n\n"
                f"<b>Tokens:</b>\n{_query_token_status()}"
            )
            send_telegram(msg)
        except Exception:
            pass


threading.Thread(target=_heartbeat, daemon=True, name="heartbeat").start()

# ════════════════════════════════════════════
#  DNS preflight
# ════════════════════════════════════════════

import socket as _socket
for _dns_attempt in range(10):
    try:
        _socket.getaddrinfo("api.github.com", 443)
        print("DNS OK — api.github.com resolves.", flush=True)
        break
    except _socket.gaierror as _e:
        _wait = min(60, 2 ** _dns_attempt)
        print(f"DNS not ready (attempt {_dns_attempt+1}/10): {_e} — retrying in {_wait}s", flush=True)
        time.sleep(_wait)
else:
    raise RuntimeError("api.github.com DNS resolution failed after 10 attempts")


# ════════════════════════════════════════════
#  Step 1: Identify missing repos
# ════════════════════════════════════════════

print("\n═══ Step 1: Identify missing repos ═══")
_progress["phase"] = "identifying missing repos"


def _extract_repo_from_url(url):
    """Extract 'owner/repo' from a GitHub URL."""
    if not isinstance(url, str):
        return None
    m = re.search(r'github\.com/repos/([^/]+/[^/]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'github\.com/([^/]+/[^/]+?)(?:/pull|$)', url)
    if m:
        return m.group(1)
    return None


# Load AIDev agent PRs — include repo_url so we can resolve names for repos
# missing from the repository.parquet metadata table.
print("Loading AIDev agent PRs (filtered, memory-efficient)...")
aidev_agent_prs = pd.read_parquet(
    "hf://datasets/hao-li/AIDev/all_pull_request.parquet",
    columns=['repo_id', 'user', 'agent', 'repo_url'],
    filters=[('agent', 'in', AGENTS_TO_KEEP)],
)
print(f"  AIDev agent PRs: {len(aidev_agent_prs):,}")

# Build agent username lookup
agent_user_map = {}
for _, row in aidev_agent_prs[['user', 'agent']].drop_duplicates().iterrows():
    agent_user_map[row['user']] = row['agent']
print(f"  Known agent usernames: {len(agent_user_map)}")

# Load AIDev repo metadata (small table — ~2800 rows)
print("Loading AIDev repository metadata...")
repo_df = pd.read_parquet("hf://datasets/hao-li/AIDev/repository.parquet")
repo_id_to_name = dict(zip(repo_df['id'], repo_df['full_name']))
repo_name_to_id = dict(zip(repo_df['full_name'], repo_df['id']))

# For repos NOT in repository.parquet, extract names from repo_url in the PR table.
# This recovers the 97%+ of repos that have PRs but no metadata entry.
print("Resolving repo names from PR URLs for repos missing from metadata...")
all_repo_ids_in_prs = set(aidev_agent_prs['repo_id'].unique())
ids_in_meta = set(repo_df['id'])
ids_missing_meta = all_repo_ids_in_prs - ids_in_meta
print(f"  Repo IDs with agent PRs: {len(all_repo_ids_in_prs):,}")
print(f"  Repo IDs in repository.parquet: {len(ids_in_meta & all_repo_ids_in_prs):,}")
print(f"  Repo IDs needing URL resolution: {len(ids_missing_meta):,}")

if ids_missing_meta:
    # Get one repo_url per repo_id for the missing ones
    missing_urls = (
        aidev_agent_prs[aidev_agent_prs['repo_id'].isin(ids_missing_meta)]
        .groupby('repo_id')['repo_url'].first()
    )
    resolved = 0
    for repo_id, url in missing_urls.items():
        name = _extract_repo_from_url(url)
        if name:
            repo_id_to_name[repo_id] = name
            repo_name_to_id[name] = repo_id
            resolved += 1
    print(f"  Resolved {resolved:,} additional repo names from URLs")
    del missing_urls

# Count agent PRs per repo and resolve names
aidev_repo_pr_counts = aidev_agent_prs.groupby('repo_id').size().reset_index(name='agent_pr_count')

# Free the large frame
del aidev_agent_prs

aidev_repo_pr_counts['full_name'] = aidev_repo_pr_counts['repo_id'].map(repo_id_to_name)
unresolved = aidev_repo_pr_counts['full_name'].isna().sum()
if unresolved:
    print(f"  WARNING: {unresolved} repo IDs could not be resolved to names (skipping)")
aidev_repo_pr_counts = aidev_repo_pr_counts.dropna(subset=['full_name'])
print(f"  AIDev repos with agent PRs (resolved names): {len(aidev_repo_pr_counts):,}")

# Load our previously collected repos.
# Check local data first, then fall back to HuggingFace.
our_repos = set()

# Check local parquet files from the previous collection cycle
LOCAL_DATA_DIR = "data_dec2024_feb2026"
local_pr_file = os.path.join(LOCAL_DATA_DIR, "all_pull_requests.parquet")
if os.path.exists(local_pr_file):
    try:
        local_df = pd.read_parquet(local_pr_file, columns=['repo_name'])
        our_repos |= set(local_df['repo_name'].unique())
        del local_df
        print(f"  Loaded {len(our_repos):,} repos from local {local_pr_file}")
    except Exception as e:
        print(f"  Could not load local parquet: {e}")

# If no local data, try HuggingFace
if not our_repos:
    print("Loading our collected repos from HuggingFace...")
    try:
        our_prs = pd.read_parquet(
            "hf://datasets/mabujadallah/GitHub-Agentic-PR-Dataset/all_pull_requests.parquet",
            columns=['repo_name'],
        )
        our_repos = set(our_prs['repo_name'].unique())
        del our_prs
        print(f"  Our collected repos (from HF): {len(our_repos):,}")
    except Exception as e:
        print(f"  Could not load our HF dataset: {e}")
        print("  Proceeding with empty set (will collect everything)")

# Also check local incremental output for already-collected repos
local_incremental = os.path.join(OUTPUT_DIR, "agent_pull_requests_incremental.parquet")
if os.path.exists(local_incremental):
    try:
        local_df = pd.read_parquet(local_incremental, columns=['repo_name'])
        local_repos = set(local_df['repo_name'].unique())
        our_repos |= local_repos
        del local_df
        print(f"  Including {len(local_repos)} repos from local incremental file")
    except Exception:
        pass

print(f"  Total already-collected repos: {len(our_repos):,}")

# Find missing repos and sort by agent PR count (highest first)
aidev_all_repo_names = set(aidev_repo_pr_counts['full_name'])
missing_repos = aidev_all_repo_names - our_repos

missing_df = aidev_repo_pr_counts[aidev_repo_pr_counts['full_name'].isin(missing_repos)].copy()
missing_df = missing_df.sort_values('agent_pr_count', ascending=False).reset_index(drop=True)

# Free memory
del aidev_repo_pr_counts, repo_df

print(f"\n  AIDev repos with agent PRs: {len(aidev_all_repo_names):,}")
print(f"  Already collected:          {len(our_repos):,}")
print(f"  Missing repos to collect:   {len(missing_df):,}")
if len(missing_df) > 0:
    print(f"  Top 10 by agent PR count:")
    for _, row in missing_df.head(10).iterrows():
        print(f"    {row['full_name']}: {row['agent_pr_count']} agent PRs")

total_expected = missing_df['agent_pr_count'].sum()
print(f"  Total expected agent PRs:   ~{total_expected:,}")

send_telegram(
    f"<b>Missing-repo collector started</b>\n"
    f"Missing repos: {len(missing_df):,}\n"
    f"Expected agent PRs: ~{total_expected:,}\n"
    f"Tokens: {len(GITHUB_TOKENS)}\n"
    f"Workers: {NUM_WORKERS}"
)

if len(missing_df) == 0:
    print("\nNothing to collect — all repos are covered!")
    send_telegram("Nothing to collect — all AIDev agent repos are covered.")
    exit(0)


# ════════════════════════════════════════════
#  Step 2: Collect agent PRs in batches
# ════════════════════════════════════════════

def classify_pr_author(username):
    if username in agent_user_map:
        return True, agent_user_map[username]
    return False, 'human'


def fetch_repo_agent_prs(repo, token_idx):
    """
    Fetch PRs for a repo, returning ONLY agent PRs.
    Returns (repo, prs_list, status) where status is one of:
      'ok'        — completed successfully
      'not_found' — repo deleted/private/transferred (don't retry)
      'error'     — transient failure (worth retrying)
    """
    agent_prs = []
    url = f"https://api.github.com/repos/{repo}/pulls"
    page = 1

    while True:
        params = {
            'state': 'all', 'sort': 'created', 'direction': 'desc',
            'per_page': 100, 'page': page
        }
        data = github_get(url, params, token_idx=token_idx)
        if data is _NOT_FOUND:
            print(f"  [404] {repo} — deleted or private, skipping", flush=True)
            return repo, [], 'not_found'
        if data is None:
            print(f"  [ERR] {repo} — API failure on page {page}", flush=True)
            return repo, agent_prs, 'error'
        if len(data) == 0:
            break

        stop = False
        for pr in data:
            created = pr.get('created_at', '')
            if created < DATE_FROM:
                stop = True
                break
            if created > DATE_TO + "T23:59:59Z":
                continue

            username = pr.get('user', {}).get('login', '')
            is_agent, agent_name = classify_pr_author(username)

            # Skip human PRs entirely — don't even build the dict
            if not is_agent:
                continue

            agent_prs.append({
                'id': pr['id'],
                'number': pr['number'],
                'title': pr.get('title', ''),
                'body': pr.get('body', ''),
                'user': username,
                'user_id': pr.get('user', {}).get('id'),
                'state': pr.get('state', ''),
                'created_at': created,
                'closed_at': pr.get('closed_at'),
                'merged_at': (
                    pr.get('pull_request', {}).get('merged_at')
                    if pr.get('pull_request')
                    else pr.get('merged_at')
                ),
                'repo_id': repo_name_to_id.get(repo),
                'repo_url': f"https://api.github.com/repos/{repo}",
                'repo_name': repo,
                'html_url': pr.get('html_url', ''),
                'is_agent': True,
                'agent': agent_name,
            })

        if stop or len(data) < 100:
            break
        page += 1

    return repo, agent_prs, 'ok'


# ── Checkpoint management ─────────────────────────────────────────

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r') as f:
            return json.load(f)
    return {
        "done_repos": [], "errors": [], "not_found": [],
        "total_prs": 0, "batches_written": 0,
    }


def save_checkpoint(ckpt):
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, 'w') as f:
        json.dump(ckpt, f)
    os.replace(tmp, CHECKPOINT_FILE)


def flush_batch(prs_batch, batch_num):
    """Write a batch of PR dicts to an individual parquet chunk file."""
    if not prs_batch:
        return
    chunk_path = os.path.join(OUTPUT_DIR, f"batch_{batch_num:05d}.parquet")
    df = pd.DataFrame(prs_batch)
    df.to_parquet(chunk_path, engine='pyarrow', index=False)
    print(f"  Flushed batch {batch_num}: {len(prs_batch)} PRs → {chunk_path}", flush=True)
    del df


# ── Main collection loop ──────────────────────────────────────────

print(f"\n═══ Step 2: Collect agent PRs ({BATCH_SIZE} repos/batch) ═══")
_progress["phase"] = "collecting agent PRs"

ckpt = load_checkpoint()
done_repos = set(ckpt["done_repos"])
error_repos = set(ckpt.get("errors", []))
not_found_repos = set(ckpt.get("not_found", []))
total_prs_collected = ckpt["total_prs"]
batch_num = ckpt["batches_written"]

# Build the work queue: missing repos not yet done/skipped, still sorted by priority
skip = done_repos | not_found_repos
work_queue = [
    row['full_name']
    for _, row in missing_df.iterrows()
    if row['full_name'] not in skip
]

_progress["repos_total"] = len(missing_df)
_progress["repos_done"] = len(done_repos)
_progress["prs_collected"] = total_prs_collected
_progress["batch"] = batch_num

print(f"  Resuming: {len(done_repos)} done, {len(not_found_repos)} not-found, {len(work_queue)} remaining, {total_prs_collected} PRs so far")

# Process in batches of BATCH_SIZE repos
for batch_start in range(0, len(work_queue), BATCH_SIZE):
    batch_repos = work_queue[batch_start:batch_start + BATCH_SIZE]
    batch_prs = []
    batch_errors = []
    batch_done = []
    batch_not_found = []

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {}
        for i, repo in enumerate(batch_repos):
            token_idx = i % len(GITHUB_TOKENS)
            futures[executor.submit(fetch_repo_agent_prs, repo, token_idx)] = repo

        for future in as_completed(futures):
            repo = futures[future]
            try:
                repo_name, prs, status = future.result()
                if status == 'not_found':
                    batch_not_found.append(repo_name)
                elif status == 'error':
                    batch_errors.append(repo_name)
                else:
                    batch_done.append(repo_name)
                    batch_prs.extend(prs)
            except Exception as e:
                print(f"  Exception for {repo}: {e}", flush=True)
                batch_errors.append(repo)

    # Flush this batch to disk
    if batch_prs:
        batch_num += 1
        flush_batch(batch_prs, batch_num)

    # Update checkpoint
    done_repos.update(batch_done)
    error_repos.update(batch_errors)
    not_found_repos.update(batch_not_found)
    total_prs_collected += len(batch_prs)

    ckpt = {
        "done_repos": list(done_repos),
        "errors": list(error_repos),
        "not_found": list(not_found_repos),
        "total_prs": total_prs_collected,
        "batches_written": batch_num,
    }
    save_checkpoint(ckpt)

    _progress["repos_done"] = len(done_repos)
    _progress["prs_collected"] = total_prs_collected
    _progress["batch"] = batch_num

    print(
        f"  Progress: {len(done_repos)}/{len(missing_df)} repos | "
        f"{total_prs_collected:,} agent PRs | batch {batch_num} | "
        f"not_found: {len(not_found_repos)} | errors: {len(error_repos)}",
        flush=True,
    )

    # Telegram update every 10 batches
    if batch_num % 10 == 0:
        send_telegram(
            f"<b>Progress update</b>\n"
            f"Repos: {len(done_repos)}/{len(missing_df)}\n"
            f"Agent PRs: {total_prs_collected:,}\n"
            f"Errors: {len(error_repos)}\n"
            f"Batch: {batch_num}"
        )

    # Release batch memory
    del batch_prs


# ════════════════════════════════════════════
#  Step 3: Retry errors (single pass)
# ════════════════════════════════════════════

if error_repos:
    print(f"\n═══ Step 3: Retrying {len(error_repos)} error repos ═══")
    _progress["phase"] = "retrying errors"
    retry_list = list(error_repos)
    still_failing = set()
    retry_prs = []

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {}
        for i, repo in enumerate(retry_list):
            token_idx = i % len(GITHUB_TOKENS)
            futures[executor.submit(fetch_repo_agent_prs, repo, token_idx)] = repo

        for future in as_completed(futures):
            repo = futures[future]
            try:
                repo_name, prs, status = future.result()
                if status == 'not_found':
                    not_found_repos.add(repo_name)
                    error_repos.discard(repo_name)
                elif status == 'error':
                    still_failing.add(repo_name)
                else:
                    done_repos.add(repo_name)
                    error_repos.discard(repo_name)
                    retry_prs.extend(prs)
            except Exception as e:
                print(f"  Retry exception for {repo}: {e}", flush=True)
                still_failing.add(repo)

    if retry_prs:
        batch_num += 1
        flush_batch(retry_prs, batch_num)
        total_prs_collected += len(retry_prs)
        del retry_prs

    error_repos = still_failing
    save_checkpoint({
        "done_repos": list(done_repos),
        "errors": list(error_repos),
        "not_found": list(not_found_repos),
        "total_prs": total_prs_collected,
        "batches_written": batch_num,
    })
    print(f"  After retry: {len(error_repos)} still failing, {len(not_found_repos)} not-found")


# ════════════════════════════════════════════
#  Step 4: Merge PR batches into single parquet
# ════════════════════════════════════════════

import pyarrow.parquet as pq
import pyarrow as pa

print(f"\n═══ Step 4: Merge {batch_num} PR batch files ═══")
_progress["phase"] = "merging PR batches"

PR_OUTPUT = os.path.join(OUTPUT_DIR, "agent_pull_requests_incremental.parquet")

chunk_files = sorted(
    f for f in os.listdir(OUTPUT_DIR)
    if f.startswith("batch_") and f.endswith(".parquet")
)

if chunk_files:
    writer = None
    for cf in chunk_files:
        table = pq.read_table(os.path.join(OUTPUT_DIR, cf))
        if writer is None:
            writer = pq.ParquetWriter(PR_OUTPUT, table.schema)
        writer.write_table(table)
        del table
    if writer is not None:
        writer.close()

    meta = pq.read_metadata(PR_OUTPUT)
    print(f"  Merged: {meta.num_rows:,} rows → {PR_OUTPUT}")

    for cf in chunk_files:
        os.remove(os.path.join(OUTPUT_DIR, cf))
    print(f"  Cleaned up {len(chunk_files)} batch files")
else:
    print("  No batch files to merge (PR parquet already up to date)")

print(f"\n  Phase 1 summary:")
print(f"  Repos collected:   {len(done_repos):,}")
print(f"  Agent PRs:         {total_prs_collected:,}")
print(f"  Not found (404):   {len(not_found_repos)}")
print(f"  Errors remaining:  {len(error_repos)}")

send_telegram(
    f"<b>Phase 1 complete — PR collection</b>\n"
    f"Repos: {len(done_repos):,}\n"
    f"Agent PRs: {total_prs_collected:,}\n"
    f"Not found: {len(not_found_repos)}\n"
    f"Errors: {len(error_repos)}"
)


# ════════════════════════════════════════════════════════════════════
#  PHASE 2: Commit metadata via GitHub API
#
#  For each agent PR, fetch its commits using the pulls/commits endpoint.
#  Memory-efficient: checkpoint every 500 PRs, write to parquet in chunks.
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("PHASE 2: Commit metadata via GitHub API")
print("=" * 60)
_progress["phase"] = "collecting commits"

# Load the merged PR parquet
if not os.path.exists(PR_OUTPUT):
    print("  No PR parquet found — skipping Phase 2 and 3")
    exit(0)

pr_df = pd.read_parquet(PR_OUTPUT)
print(f"  Total agent PRs to fetch commits for: {len(pr_df):,}")

COMMITS_CKPT = os.path.join(OUTPUT_DIR, "checkpoint_commits.json")
COMMITS_OUTPUT = os.path.join(OUTPUT_DIR, "pr_commits_incremental.parquet")
COMMITS_CHUNK_DIR = os.path.join(OUTPUT_DIR, "commit_chunks")
os.makedirs(COMMITS_CHUNK_DIR, exist_ok=True)


def fetch_pr_commits(pr_id, repo_name, pr_number, token_idx):
    """Fetch commits for a single PR via GitHub API."""
    url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}/commits"
    commits = github_get_all_pages(url, token_idx=token_idx)
    if commits is None:
        return pr_id, [], True  # error

    results = []
    for c in commits:
        results.append({
            'sha': c['sha'],
            'pr_id': pr_id,
            'author': (
                (c.get('author') or {}).get('login')
                or c.get('commit', {}).get('author', {}).get('name', '')
            ),
            'committer': (
                (c.get('committer') or {}).get('login')
                or c.get('commit', {}).get('committer', {}).get('name', '')
            ),
            'message': c.get('commit', {}).get('message', ''),
        })
    return pr_id, results, False


# Load checkpoint
commits_done_ids = set()
commit_errors = []
commits_chunk_num = 0

if os.path.exists(COMMITS_CKPT):
    try:
        with open(COMMITS_CKPT, 'r') as f:
            ckpt = json.load(f)
        commits_done_ids = set(ckpt.get('done_pr_ids', []))
        commit_errors = ckpt.get('errors', [])
        commits_chunk_num = ckpt.get('chunks_written', 0)
        print(f"  RESUMED: {len(commits_done_ids):,}/{len(pr_df):,} PRs done, "
              f"{commits_chunk_num} chunks, {len(commit_errors)} errors")
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  WARNING: Commits checkpoint corrupted ({e}), starting fresh.")

remaining_prs = pr_df[~pr_df['id'].isin(commits_done_ids)].reset_index(drop=True)
print(f"  PRs remaining: {len(remaining_prs):,}")

COMMIT_BATCH_SIZE = 500  # PRs per commit chunk flush

t_commits_start = time.time()
completed_commits = 0
batch_commit_rows = []

with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
    futures = {}
    for idx, row in enumerate(remaining_prs.itertuples(index=False)):
        token_idx = idx % len(GITHUB_TOKENS)
        fut = executor.submit(fetch_pr_commits, row.id, row.repo_name, row.number, token_idx)
        futures[fut] = row.id

    for fut in as_completed(futures):
        completed_commits += 1
        pr_id = futures[fut]
        try:
            pr_id, commits, had_error = fut.result()
            if had_error:
                commit_errors.append(int(pr_id))
            else:
                batch_commit_rows.extend(commits)
            commits_done_ids.add(int(pr_id))
        except Exception as e:
            commit_errors.append(int(pr_id))
            commits_done_ids.add(int(pr_id))

        # Flush + checkpoint every COMMIT_BATCH_SIZE PRs
        if completed_commits % COMMIT_BATCH_SIZE == 0 or completed_commits == len(remaining_prs):
            # Flush accumulated rows to parquet chunk
            if batch_commit_rows:
                commits_chunk_num += 1
                chunk_path = os.path.join(COMMITS_CHUNK_DIR, f"commits_{commits_chunk_num:05d}.parquet")
                pd.DataFrame(batch_commit_rows).to_parquet(chunk_path, engine='pyarrow', index=False)
                batch_commit_rows = []

            # Save checkpoint
            tmp = COMMITS_CKPT + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({
                    'done_pr_ids': [int(x) for x in commits_done_ids],
                    'errors': commit_errors,
                    'chunks_written': commits_chunk_num,
                }, f)
            os.replace(tmp, COMMITS_CKPT)

            elapsed = time.time() - t_commits_start
            rate = completed_commits / max(elapsed, 1) * 3600
            print(f"  Commits: {len(commits_done_ids):,}/{len(pr_df):,} PRs | "
                  f"chunk {commits_chunk_num} | {rate:.0f} PRs/hr | "
                  f"errors: {len(commit_errors)}", flush=True)

# Retry commit errors (single pass)
if commit_errors:
    print(f"\n  Retrying {len(commit_errors)} commit errors...")
    retry_ids = set(commit_errors)
    commit_errors = []
    retry_prs_df = pr_df[pr_df['id'].isin(retry_ids)].reset_index(drop=True)

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {}
        for idx, row in enumerate(retry_prs_df.itertuples(index=False)):
            token_idx = idx % len(GITHUB_TOKENS)
            fut = executor.submit(fetch_pr_commits, row.id, row.repo_name, row.number, token_idx)
            futures[fut] = row.id

        for fut in as_completed(futures):
            pr_id = futures[fut]
            try:
                pr_id, commits, had_error = fut.result()
                if had_error:
                    commit_errors.append(int(pr_id))
                else:
                    batch_commit_rows.extend(commits)
            except Exception:
                commit_errors.append(int(pr_id))

    if batch_commit_rows:
        commits_chunk_num += 1
        chunk_path = os.path.join(COMMITS_CHUNK_DIR, f"commits_{commits_chunk_num:05d}.parquet")
        pd.DataFrame(batch_commit_rows).to_parquet(chunk_path, engine='pyarrow', index=False)
        batch_commit_rows = []

    # Save final checkpoint
    tmp = COMMITS_CKPT + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({
            'done_pr_ids': [int(x) for x in commits_done_ids],
            'errors': commit_errors,
            'chunks_written': commits_chunk_num,
        }, f)
    os.replace(tmp, COMMITS_CKPT)
    print(f"  After retry: {len(commit_errors)} still failing")

# Merge commit chunks
print(f"\n  Merging {commits_chunk_num} commit chunk files...")
commit_chunk_files = sorted(
    f for f in os.listdir(COMMITS_CHUNK_DIR)
    if f.endswith(".parquet")
)

if commit_chunk_files:
    writer = None
    for cf in commit_chunk_files:
        table = pq.read_table(os.path.join(COMMITS_CHUNK_DIR, cf))
        if writer is None:
            writer = pq.ParquetWriter(COMMITS_OUTPUT, table.schema)
        writer.write_table(table)
        del table
    if writer is not None:
        writer.close()

    meta = pq.read_metadata(COMMITS_OUTPUT)
    print(f"  Merged: {meta.num_rows:,} commits → {COMMITS_OUTPUT}")

    for cf in commit_chunk_files:
        os.remove(os.path.join(COMMITS_CHUNK_DIR, cf))
else:
    print("  No commit chunks to merge")

commits_elapsed = time.time() - t_commits_start
print(f"  Phase 2 done in {commits_elapsed/60:.1f} min. Errors: {len(commit_errors)}")

send_telegram(
    f"<b>Phase 2 complete — commits</b>\n"
    f"PRs processed: {len(commits_done_ids):,}\n"
    f"Errors: {len(commit_errors)}"
)

del pr_df  # free memory before Phase 3


# ════════════════════════════════════════════════════════════════════
#  PHASE 3: Commit details via local git clones
#
#  Mirror-clone each repo, extract metadata + numstat + file statuses
#  + patches in batch, write per-repo parquet chunks, delete clone.
# ════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("PHASE 3: Commit details via local git clones")
print("=" * 60)
_progress["phase"] = "local-git-details"

if not os.path.exists(COMMITS_OUTPUT):
    print("  No commits parquet found — skipping Phase 3")
    exit(0)

commits_df = pd.read_parquet(COMMITS_OUTPUT)
print(f"  Total commits: {len(commits_df):,}")

if len(commits_df) == 0:
    print("  No commits to extract details for. Done.")
    exit(0)

# Reload PR data to map pr_id → repo_name
pr_df = pd.read_parquet(PR_OUTPUT, columns=['id', 'repo_name'])
_pr_repo_map = dict(zip(pr_df['id'], pr_df['repo_name']))
del pr_df

# Build sha → [pr_id, ...] and group unique SHAs by repo
sha_to_pr_ids = defaultdict(list)
for row in commits_df.itertuples(index=False):
    sha_to_pr_ids[row.sha].append(row.pr_id)
del commits_df

repo_shas = defaultdict(list)
seen_shas = set()
for sha, pr_ids in sha_to_pr_ids.items():
    if sha in seen_shas:
        continue
    seen_shas.add(sha)
    repo_name = None
    for pid in pr_ids:
        repo_name = _pr_repo_map.get(pid)
        if repo_name:
            break
    if repo_name:
        repo_shas[repo_name].append(sha)
del seen_shas

total_unique_shas = sum(len(v) for v in repo_shas.values())
total_detail_repos = len(repo_shas)
print(f"  Unique SHAs: {total_unique_shas:,} across {total_detail_repos:,} repos")
print(f"  Config: CLONE_WORKERS={CLONE_WORKERS}, INCLUDE_PATCH={INCLUDE_PATCH}, "
      f"SHA_BATCH_SIZE={SHA_BATCH_SIZE}, DELETE_AFTER_EXTRACT={DELETE_AFTER_EXTRACT}")

os.makedirs(REPO_CACHE_DIR, exist_ok=True)
os.makedirs(DETAILS_CHUNK_DIR, exist_ok=True)

# ── Git helpers ───────────────────────────────────────────────────

_GIT_STATUS_MAP = {
    'A': 'added', 'M': 'modified', 'D': 'removed',
    'R': 'renamed', 'C': 'copied', 'T': 'changed',
}

_COMMIT_SEP = '---COMMIT_BOUNDARY_8f3a1b---'
_MSG_END    = '---MSG_END_8f3a1b---'


def sanitize_repo_path(repo_name):
    safe = repo_name.replace('/', '__')
    return Path(REPO_CACHE_DIR) / f"{safe}.git"


def clone_or_update_repo(repo_name):
    repo_path = sanitize_repo_path(repo_name)
    url = f"https://github.com/{repo_name}.git"
    try:
        if repo_path.exists() and (repo_path / 'HEAD').exists():
            result = subprocess.run(
                ['git', '-C', str(repo_path), 'remote', 'update', '--prune'],
                capture_output=True, text=True, timeout=FETCH_TIMEOUT,
            )
            if result.returncode == 0:
                subprocess.run(
                    ['git', '-C', str(repo_path), 'fetch', 'origin',
                     '+refs/pull/*/head:refs/pull/*/head'],
                    capture_output=True, text=True, timeout=FETCH_TIMEOUT,
                )
            if result.returncode != 0:
                return repo_path, False, f"fetch failed: {result.stderr.strip()}"
            return repo_path, True, ""
        else:
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ['git', 'clone', '--mirror', '--filter=blob:none', url, str(repo_path)],
                capture_output=True, text=True, timeout=CLONE_TIMEOUT,
            )
            if result.returncode == 0:
                subprocess.run(
                    ['git', '-C', str(repo_path), 'fetch', 'origin',
                     '+refs/pull/*/head:refs/pull/*/head'],
                    capture_output=True, text=True, timeout=FETCH_TIMEOUT,
                )
            if result.returncode != 0:
                return repo_path, False, f"clone failed: {result.stderr.strip()}"
            return repo_path, True, ""
    except subprocess.TimeoutExpired:
        return repo_path, False, "timeout"
    except Exception as e:
        return repo_path, False, str(e)


def _parse_numstat_block(lines):
    files = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split('\t', 2)
        if len(parts) < 3:
            continue
        add_str, del_str, fname = parts
        add = int(add_str) if add_str != '-' else 0
        dlt = int(del_str) if del_str != '-' else 0
        files.append((add, dlt, fname))
    return files


def extract_commit_metadata_and_numstat(repo_path, shas):
    if not shas:
        return {}
    fmt = (
        f'{_COMMIT_SEP}%n'
        '%H%n'
        '%an%n'
        '%cn%n'
        '%B'
        f'{_MSG_END}'
    )
    stdin_data = ('\n'.join(shas) + '\n').encode()
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_path), 'log',
             '--no-walk', '--stdin', '--no-renames',
             f'--format={fmt}', '--numstat'],
            input=stdin_data, capture_output=True, timeout=EXTRACT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT: git log --numstat on {repo_path}", flush=True)
        return {}

    stdout = result.stdout.decode('utf-8', errors='replace')
    commits = {}
    blocks = stdout.split(_COMMIT_SEP)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        msg_end_pos = block.find(_MSG_END)
        if msg_end_pos == -1:
            continue
        header_and_msg = block[:msg_end_pos]
        numstat_text = block[msg_end_pos + len(_MSG_END):]
        lines = header_and_msg.split('\n', 3)
        if len(lines) < 4:
            continue
        sha = lines[0].strip()
        author = lines[1].strip()
        committer = lines[2].strip()
        message = lines[3].strip()
        files = _parse_numstat_block(numstat_text.strip().split('\n'))
        commits[sha] = {
            'author': author, 'committer': committer,
            'message': message, 'files': files,
        }
    return commits


def extract_file_statuses(repo_path, shas):
    if not shas:
        return {}
    stdin_data = ('\n'.join(shas) + '\n').encode()
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_path), 'diff-tree',
             '-r', '--no-renames', '--name-status', '--stdin', '--root'],
            input=stdin_data, capture_output=True, timeout=EXTRACT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT: git diff-tree on {repo_path}", flush=True)
        return {}

    stdout = result.stdout.decode('utf-8', errors='replace')
    statuses = {}
    current_sha = None
    for line in stdout.split('\n'):
        line = line.strip()
        if not line:
            continue
        if len(line) >= 40 and all(c in '0123456789abcdef' for c in line[:40]):
            current_sha = line[:40]
            if current_sha not in statuses:
                statuses[current_sha] = {}
            continue
        if current_sha is None:
            continue
        parts = line.split('\t', 1)
        if len(parts) == 2:
            status_letter = parts[0].strip()
            fname = parts[1].strip()
            statuses[current_sha][fname] = _GIT_STATUS_MAP.get(
                status_letter[0] if status_letter else '', status_letter)
    return statuses


def extract_patches_for_sha(repo_path, sha):
    if not INCLUDE_PATCH:
        return {}
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_path), 'log', '-1',
             '--no-renames', '--format=', '-p', sha],
            capture_output=True, timeout=EXTRACT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {}
    if result.returncode != 0:
        return {}

    stdout = result.stdout.decode('utf-8', errors='replace')
    patches = {}
    diff_blocks = re.split(r'^diff --git ', stdout, flags=re.MULTILINE)
    for block in diff_blocks:
        if not block.strip():
            continue
        first_line_end = block.find('\n')
        if first_line_end == -1:
            continue
        header = block[:first_line_end]
        parts = header.split(' b/', 1)
        if len(parts) < 2:
            continue
        fname = parts[1].strip()
        patch_text = block[first_line_end + 1:]
        if len(patch_text.encode('utf-8', errors='replace')) > MAX_PATCH_BYTES:
            patches[fname] = None
        else:
            patches[fname] = patch_text
    return patches


def fetch_commit_from_api(repo_name, sha, token_idx):
    """Fallback: fetch a single commit via GitHub API when local git fails."""
    url = f"https://api.github.com/repos/{repo_name}/commits/{sha}"
    data = github_get(url, token_idx=token_idx)
    if not data or data is _NOT_FOUND:
        return None

    commit_data = data.get('commit', {})
    stats = data.get('stats', {})
    files = []
    for f in data.get('files', []):
        add = f.get('additions', 0)
        dlt = f.get('deletions', 0)
        files.append({
            'filename': f.get('filename'),
            'status': f.get('status', 'modified'),
            'additions': add, 'deletions': dlt,
            'changes': add + dlt,
            'patch': f.get('patch') if INCLUDE_PATCH else None,
        })
    return {
        'sha': sha,
        'author': commit_data.get('author', {}).get('name', ''),
        'committer': commit_data.get('committer', {}).get('name', ''),
        'message': commit_data.get('message', ''),
        'commit_stats_total': stats.get('total', 0),
        'commit_stats_additions': stats.get('additions', 0),
        'commit_stats_deletions': stats.get('deletions', 0),
        'files_detailed': files,
    }


def process_repo_details(repo_name, shas, sha_to_pr_ids_map, repo_path, token_idx=0):
    """Extract commit details for SHAs in a repo. Yields (rows, missing) per batch."""
    total_shas = len(shas)
    processed = 0
    for batch_start in range(0, total_shas, SHA_BATCH_SIZE):
        batch = shas[batch_start:batch_start + SHA_BATCH_SIZE]
        detail_rows = []
        missing_shas = []

        meta = extract_commit_metadata_and_numstat(repo_path, batch)
        statuses = extract_file_statuses(repo_path, batch)

        for sha in batch:
            if sha not in meta:
                api_info = fetch_commit_from_api(repo_name, sha, token_idx)
                if not api_info:
                    missing_shas.append(sha)
                    continue
                base = {
                    'sha': sha,
                    'author': api_info['author'],
                    'committer': api_info['committer'],
                    'message': api_info['message'],
                    'commit_stats_total': api_info['commit_stats_total'],
                    'commit_stats_additions': api_info['commit_stats_additions'],
                    'commit_stats_deletions': api_info['commit_stats_deletions'],
                }
                pr_ids = sha_to_pr_ids_map.get(sha, [None])
                if not api_info['files_detailed']:
                    for pid in pr_ids:
                        detail_rows.append({
                            **base, 'pr_id': pid,
                            'filename': None, 'status': None, 'additions': None,
                            'deletions': None, 'changes': None, 'patch': None,
                        })
                else:
                    for f in api_info['files_detailed']:
                        for pid in pr_ids:
                            detail_rows.append({
                                **base, 'pr_id': pid,
                                'filename': f['filename'], 'status': f['status'],
                                'additions': f['additions'], 'deletions': f['deletions'],
                                'changes': f['changes'], 'patch': f['patch'],
                            })
                continue

            info = meta[sha]
            files = info['files']
            sha_statuses = statuses.get(sha, {})
            patches = extract_patches_for_sha(repo_path, sha) if INCLUDE_PATCH else {}

            total_add = sum(f[0] for f in files)
            total_del = sum(f[1] for f in files)

            base = {
                'sha': sha,
                'author': info['author'],
                'committer': info['committer'],
                'message': info['message'],
                'commit_stats_total': total_add + total_del,
                'commit_stats_additions': total_add,
                'commit_stats_deletions': total_del,
            }

            pr_ids = sha_to_pr_ids_map.get(sha, [None])

            if not files:
                for pid in pr_ids:
                    detail_rows.append({
                        **base, 'pr_id': pid,
                        'filename': None, 'status': None,
                        'additions': None, 'deletions': None,
                        'changes': None, 'patch': None,
                    })
            else:
                for add, dlt, fname in files:
                    status = sha_statuses.get(fname, 'modified')
                    patch = patches.get(fname) if INCLUDE_PATCH else None
                    for pid in pr_ids:
                        detail_rows.append({
                            **base, 'pr_id': pid,
                            'filename': fname, 'status': status,
                            'additions': add, 'deletions': dlt,
                            'changes': add + dlt, 'patch': patch,
                        })

        processed += len(batch)
        yield detail_rows, missing_shas


# ── Phase 3 checkpoint ───────────────────────────────────────────

DETAILS_CKPT_FILE = os.path.join(OUTPUT_DIR, "checkpoint_details.json")
DETAILS_OUTPUT = os.path.join(OUTPUT_DIR, "pr_commit_details_incremental.parquet")


def _save_details_checkpoint(done, errors, missing):
    tmp = DETAILS_CKPT_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({
            'done_repos': sorted(done),
            'error_repos': sorted(errors),
            'missing_shas': sorted(missing),
        }, f)
    os.replace(tmp, DETAILS_CKPT_FILE)


# Load checkpoint
detail_done_repos = set()
detail_error_repos = set()
all_missing_shas = set()
if os.path.exists(DETAILS_CKPT_FILE):
    try:
        with open(DETAILS_CKPT_FILE, 'r') as f:
            ckpt = json.load(f)
        detail_done_repos = set(ckpt.get('done_repos', []))
        detail_error_repos = set(ckpt.get('error_repos', []))
        all_missing_shas = set(ckpt.get('missing_shas', []))
        print(f"  RESUMED: {len(detail_done_repos)}/{total_detail_repos} repos done, "
              f"{len(detail_error_repos)} errors, {len(all_missing_shas)} missing SHAs")
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  WARNING: Details checkpoint corrupted ({e}), starting fresh.")

remaining_detail_repos = [r for r in repo_shas if r not in detail_done_repos]
print(f"  Repos remaining: {len(remaining_detail_repos):,}")

send_telegram(
    f"<b>Phase 3 started: commit details via local git</b>\n"
    f"Repos: {len(remaining_detail_repos):,} (of {total_detail_repos:,})\n"
    f"Unique SHAs: {total_unique_shas:,}\n"
    f"Workers: {CLONE_WORKERS}"
)

# ── Pipeline: clone → extract → write chunk → delete ─────────────

detail_t = time.time()
total_detail_rows = 0
detail_repos_processed = 0
_details_lock = threading.Lock()


def _process_one_repo(repo_name, token_idx):
    """Clone → extract → write chunk → delete for a single repo."""
    shas = repo_shas[repo_name]
    repo_path = sanitize_repo_path(repo_name)

    repo_path, ok, err = clone_or_update_repo(repo_name)
    if not ok:
        return (repo_name, 0, list(shas), err, False)

    total_missing = []
    num_rows = 0
    try:
        for i, (rows, missing) in enumerate(
            process_repo_details(repo_name, shas, sha_to_pr_ids, repo_path, token_idx)
        ):
            total_missing.extend(missing)
            if rows:
                chunk_name = repo_name.replace('/', '__') + f'_part{i}.parquet'
                chunk_path = Path(DETAILS_CHUNK_DIR) / chunk_name
                pd.DataFrame(rows).to_parquet(str(chunk_path), index=False)
                num_rows += len(rows)
    except Exception as e:
        if DELETE_AFTER_EXTRACT and repo_path.exists():
            shutil.rmtree(repo_path, ignore_errors=True)
        return (repo_name, num_rows, total_missing + list(shas), str(e), True)

    if DELETE_AFTER_EXTRACT and repo_path.exists():
        shutil.rmtree(repo_path, ignore_errors=True)

    return (repo_name, num_rows, total_missing, None, True)


with ThreadPoolExecutor(max_workers=CLONE_WORKERS) as executor:
    futures = {}
    for i, rn in enumerate(remaining_detail_repos):
        t_idx = i % len(GITHUB_TOKENS)
        futures[executor.submit(_process_one_repo, rn, t_idx)] = rn

    for fut in as_completed(futures):
        detail_repos_processed += 1
        rn, num_rows, missing_or_shas, err_msg, clone_ok = fut.result()

        with _details_lock:
            if err_msg is not None:
                label = "CLONE FAIL" if not clone_ok else "EXTRACT ERROR"
                print(f"  {label} [{detail_repos_processed}/{len(remaining_detail_repos)}] "
                      f"{rn}: {err_msg}", flush=True)
                all_missing_shas.update(missing_or_shas)
                detail_error_repos.add(rn)
            else:
                all_missing_shas.update(missing_or_shas)
                total_detail_rows += num_rows

            detail_done_repos.add(rn)

            if detail_repos_processed % 50 == 0 or detail_repos_processed == len(remaining_detail_repos):
                elapsed = time.time() - detail_t
                rate = detail_repos_processed / max(elapsed, 1) * 3600
                print(f"  Details: {len(detail_done_repos)}/{total_detail_repos} repos | "
                      f"{total_detail_rows:,} rows | {len(all_missing_shas)} missing | "
                      f"{rate:.0f} repos/hr", flush=True)
                _save_details_checkpoint(detail_done_repos, detail_error_repos, all_missing_shas)

                if detail_repos_processed % 100 == 0:
                    send_telegram(
                        f"<b>Details progress</b>\n"
                        f"Repos: {len(detail_done_repos)}/{total_detail_repos}\n"
                        f"Rows: {total_detail_rows:,}\n"
                        f"Missing SHAs: {len(all_missing_shas):,}"
                    )

# Retry failed repos
if detail_error_repos:
    print(f"\n  Retrying {len(detail_error_repos)} failed detail repos...")
    repos_to_retry = list(detail_error_repos)
    detail_error_repos.clear()

    with ThreadPoolExecutor(max_workers=CLONE_WORKERS) as executor:
        retry_futures = {}
        for i, rn in enumerate(repos_to_retry):
            t_idx = i % len(GITHUB_TOKENS)
            retry_futures[executor.submit(_process_one_repo, rn, t_idx)] = rn

        for fut in as_completed(retry_futures):
            rn, num_rows, missing_or_shas, err_msg, clone_ok = fut.result()
            with _details_lock:
                if err_msg is not None:
                    all_missing_shas.update(missing_or_shas)
                    detail_error_repos.add(rn)
                else:
                    all_missing_shas.update(missing_or_shas)
                    total_detail_rows += num_rows
                    detail_done_repos.add(rn)

    _save_details_checkpoint(detail_done_repos, detail_error_repos, all_missing_shas)
    print(f"  After retry: {len(detail_error_repos)} still failing")

# Final checkpoint
_save_details_checkpoint(detail_done_repos, detail_error_repos, all_missing_shas)

detail_elapsed = time.time() - detail_t
print(f"\n  Detail extraction done in {detail_elapsed/60:.1f} min")
print(f"  Repos: {len(detail_done_repos)} ({len(detail_error_repos)} errors)")
print(f"  Missing SHAs: {len(all_missing_shas)}")

# Merge detail chunks
print(f"\n  Merging detail chunk files...")
import pyarrow.dataset as ds

detail_chunk_files = sorted(Path(DETAILS_CHUNK_DIR).glob('*.parquet'))
if detail_chunk_files:
    dataset = ds.dataset(DETAILS_CHUNK_DIR, format="parquet")
    schema = dataset.schema
    total_rows = 0
    with pq.ParquetWriter(DETAILS_OUTPUT, schema) as writer:
        for batch in dataset.to_batches(batch_size=50000):
            writer.write_batch(batch)
            total_rows += batch.num_rows
    print(f"  Merged: {total_rows:,} rows → {DETAILS_OUTPUT}")

    for f in detail_chunk_files:
        f.unlink()
    print(f"  Cleaned up {len(detail_chunk_files)} chunk files")
else:
    print("  No detail chunks to merge")

send_telegram(
    f"<b>Phase 3 complete — commit details</b>\n"
    f"Repos: {len(detail_done_repos):,}\n"
    f"Detail rows: {total_detail_rows:,}\n"
    f"Missing SHAs: {len(all_missing_shas):,}\n"
    f"Errors: {len(detail_error_repos)}"
)


# ════════════════════════════════════════════
#  Final Summary
# ════════════════════════════════════════════

print(f"\n{'═' * 60}")
print(f"  All phases complete!")
print(f"  PRs:             {PR_OUTPUT}")
print(f"  Commits:         {COMMITS_OUTPUT}")
print(f"  Commit details:  {DETAILS_OUTPUT}")
print(f"{'═' * 60}")

send_telegram(
    f"<b>All phases complete!</b>\n"
    f"Output: {OUTPUT_DIR}/\n\n"
    f"<b>Token status:</b>\n{_query_token_status()}"
)
