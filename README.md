# Agentic PR: A Large-Scale Dataset of AI-Generated and Human Pull Requests on GitHub

A large-scale dataset of AI-generated and human pull requests collected from GitHub, including PR metadata, commits, diffs, and repository information. This project provides an end-to-end pipeline: from multi-threaded data collection, through PR classification, to statistical analysis and academic report generation — comparing AI coding agents (Copilot, Cursor, Claude Code, Devin) against human contributors.

## Dataset Overview

| Metric | Value |
|--------|-------|
| **Collection Period** | December 2024 – February 2026 (15 months) |
| **Total Fix PRs** | 349,410 |
| **Agent PRs** | 48,624 (13.9%) |
| **Human PRs** | 300,786 (86.1%) |
| **Repositories** | 1,616 |
| **Total Commits** | 913,847 |
| **Unique Committers** | 31,494 |

**Agent breakdown:** Cursor (20,516) · Claude Code (16,990) · Copilot (8,760) · Devin (2,358)

## Features

- **High-Speed Collection**: Uses parallel threads and transparent GitHub API token rotation to maximize collection speed while respecting rate limits.
- **Resilient Checkpointing**: Automatically saves checkpoints during large collection runs, allowing you to seamlessly restart or recover from network interruptions.
- **Hot-Reload Configuration**: Automatically detects changes in the `.env` file (e.g., adding a new GitHub token) without restarting the running script.
- **Telegram Heartbeat Monitoring**: Optional periodic progress updates and alerts via a Telegram bot.
- **Local Git Mirroring**: Downloads commits via local Git mirrors (`repo_cache`) and extracts patch data securely, bypassing API rate limits for commit details.
- **PR Classification**: Regex-based classification of "fix" PRs using conventional commit patterns and natural language synonyms.
- **Statistical Analysis**: Automated report generation with Chi-squared tests, Mann-Whitney U tests, violin plots, and trend charts.

## Project Structure

```
├── collector.py                 # Multi-phase PR & commit data collection
├── collect_missing.py           # Incrementally collect missing agent PRs from uncovered repos
├── classify_fix_prs.py          # Classify PRs as fix/other via regex
├── hybrid_classify.py           # Classify PR commits by type using Copilot CLI
├── export_aidev_repos.py        # Export repository metadata from AIDev dataset
├── calculate_overlap.py         # Calculate overlap metrics between local and remote datasets
├── compare.py                   # Compare datasets and generate CSV breakdowns & summaries
├── generate_report.py           # Generate statistical reports & figures
├── generate_report.ipynb        # Interactive report (full sample)
├── generate_report_matched.ipynb# Interactive report (matched sample)
├── dataset_comparison_results.ipynb  # Statistical comparison between our dataset and AIDev
├── dataset_overlap_analysis.ipynb    # Dataset overlap analysis with intersection/union metrics
├── aidev_acceptance_rate.ipynb   # Acceptance rate analysis on AIDev data
├── own_data_acceptance_rate.ipynb# Acceptance rate analysis on own data (Dec 2024 – Jul 2025)
├── rest_months_acceptance_rate.ipynb # Acceptance rate analysis (Aug 2025 – Feb 2026)
├── aidev_repositories.csv       # Repository metadata lookup table
├── .env.example                 # Template for environment variables
├── requirements.txt             # Python dependencies
├── results/                     # Generated reports and figures
│   ├── report.txt               # Full-sample report
│   ├── matched_report.txt       # Matched-sample report
│   ├── aidev_report.txt         # AIDev baseline report
│   ├── own_data_report.txt      # Own data (AIDev date range) report
│   ├── rest_months_report.txt   # Remaining months report
│   ├── report_figures/          # Plots for full-sample analysis
│   ├── matched_report_figures/  # Plots for matched-sample analysis
│   ├── aidev_report_figures/    # Plots for AIDev analysis
│   ├── own_data_report_figures/ # Plots for own data analysis
│   └── rest_months_report_figures/ # Plots for remaining months analysis
└── LICENSE                      # CC BY 4.0
```

## Prerequisites

- Python 3.8+
- [Git](https://git-scm.com/downloads) installed and available in the system PATH.

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/mabujadallah/Agentic-PR-A-Large-Scale-Dataset-of-AI-Generated-and-Human-Pull-Requests-on-GitHub.git
   cd Agentic-PR-A-Large-Scale-Dataset-of-AI-Generated-and-Human-Pull-Requests-on-GitHub
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment variables by copying the example file and editing it:
   ```bash
   cp .env.example .env
   ```
   Then fill in your tokens:
   ```
   GITHUB_TOKENS=ghp_YOUR_TOKEN_1,ghp_YOUR_TOKEN_2
   ```
   Optional variables:
   ```
   HTTPS_PROXY=http://user:pass@host:port
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

## Usage

### 1. Data Collection

```bash
python collector.py
```

Runs a multi-phase pipeline that automatically discovers repositories from the [AIDev dataset](https://huggingface.co/datasets/hao-li/AIDev):

| Phase | Description |
|-------|-------------|
| **Phase 0** | Validation sprint — quick one-month collection against AIDev baseline |
| **Phase 1** | Multi-threaded PR metadata collection across all target repositories |
| **Phase 2** | Per-PR commit fetching with retry on errors |
| **Phase 3** | Local git clone → batch extract → Parquet write pipeline |

**Outputs** (in `data_dec2024_feb2026/`):
- `all_pull_requests.parquet` — All PRs (agent + human)
- `agent_pull_requests.parquet` — Agent PRs only
- `human_pull_requests.parquet` — Human PRs only
- `pr_commits.parquet` — Commit metadata per PR
- `pr_commit_details.parquet` — Per-file change metrics (additions, deletions, patches)

Checkpoints are saved as `checkpoint_*.json` files. If the process is interrupted, re-running the script resumes from the last checkpoint.

### 2. Collect Missing Repositories

```bash
python collect_missing.py
```

Incrementally collects missing agent PRs from repositories not yet covered in the dataset by comparing against the AIDev baseline and prioritizing by agent-PR count.

### 3. PR Classification

```bash
python classify_fix_prs.py
```

Classifies PRs as "fix" or "other" using regex matching on PR titles (conventional commits like `fix:`, plus natural language patterns such as `bug fix`, `hotfix`, `resolve`, `patch`, etc.).

**Outputs:**
- `fix_classified_prs.parquet` — All PRs with fix/other labels
- `fix_prs_only.parquet` — Fix PRs subset
- `fix_pr_commits.parquet` — Commits for fix PRs
- `fix_pr_commit_details.parquet` — Commit details for fix PRs

### 4. Hybrid Classification

```bash
python hybrid_classify.py
```

Classifies PR commits by type using the Copilot CLI command with token limits, retry logic, and generates formatted classification reports.

### 5. Repository Metadata Export

```bash
python export_aidev_repos.py
```

Creates `aidev_repositories.csv` with normalized repository metadata (repo ID, name, PR count, language, stars, forks, license).

### 6. Dataset Comparison & Overlap

```bash
python calculate_overlap.py
python compare.py
```

- `calculate_overlap.py` — Loads local and remote PR datasets, calculates overlap metrics, and sends comparison results via Telegram.
- `compare.py` — Compares three PR datasets and generates CSV breakdowns (per-repo, status summary, monthly trends) with Telegram integration.

Or run the Jupyter notebooks interactively:

| Notebook | Analysis Scope |
|----------|---------------|
| `dataset_comparison_results.ipynb` | Statistical and visual comparison between our dataset and AIDev |
| `dataset_overlap_analysis.ipynb` | Overlap analysis with intersection/union metrics and visualizations |

### 7. Report Generation

Generate the statistical analysis report:

```bash
python generate_report.py
```

Or run the Jupyter notebooks interactively for different analysis subsets:

| Notebook | Analysis Scope |
|----------|---------------|
| `generate_report.ipynb` | Full sample analysis |
| `generate_report_matched.ipynb` | Matched sample (equal agent/human counts) |
| `aidev_acceptance_rate.ipynb` | AIDev baseline data |
| `own_data_acceptance_rate.ipynb` | Own collection, AIDev date range (Dec 2024 – Jul 2025) |
| `rest_months_acceptance_rate.ipynb` | Own collection, remaining months (Aug 2025 – Feb 2026) |

## Research Questions

The analysis addresses three research questions:

- **RQ1**: How do agent fix PRs differ from human fix PRs in change size? *(Files changed, lines added/deleted, description length)*
- **RQ2**: To what extent are agent-generated PRs rejected compared to human PRs? *(Merge rates, per-agent breakdown, Chi-squared test)*
- **RQ3**: What proportion of agent PRs are accepted without revisions? *(Revision commit counts, Mann-Whitney U test)*

## License

This project is licensed under [CC BY 4.0](LICENSE).
