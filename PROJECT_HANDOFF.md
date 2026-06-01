# Project Handoff: GitHub Agentic PR Dataset

## What We Did

### 1. Data Collection
We built an end-to-end pipeline to collect and analyze AI-generated vs. human Pull Requests from GitHub.

- **Collection period:** December 2024 – February 2026 (15 months)
- **Agents tracked:** Copilot, Cursor, Claude Code, Devin
- **Tools used:** `collector.py` (multi-threaded, API token rotation, checkpointing), `collect_missing.py` (gap filling), `classify_fix_prs.py` (regex-based fix PR classification)

---

### 2. Datasets Analyzed

We worked with **four distinct datasets** to cross-validate findings:

| Dataset | PRs | Period | Notes |
|---|---|---|---|
| **Full Sample (Our Data)** | 349,410 fix PRs | Dec 2024 – Feb 2026 | 48,624 agent + 300,786 human |
| **Matched Sample** | 232,022 fix PRs | Dec 2024 – Feb 2026 | 116,011 agent + 116,011 human (random seed=42) |
| **AIDev Baseline** | 23,845 fix PRs | Dec 2024 – Jul 2025 | External dataset from `hao-li/AIDev` on HuggingFace |
| **Own Data (AIDev period)** | 5,240 fix PRs | Dec 2024 – Jul 2025 | Our data over same window as AIDev |
| **Rest Months** | 24,205 fix PRs | Aug 2025 – Feb 2026 | Months beyond AIDev coverage |

---

### 3. Research Questions (RQs)

#### RQ1 — How do Agent fix PRs differ from Human fix PRs in change size?

| Metric | Agent (median) | Human (median) |
|---|---|---|
| Files changed | 2 | 2 |
| Lines added | 28–57 | 20–22 |
| Lines deleted | 10–12 | 7 |
| PR description length | 142–214 words | 77–85 words |

**Key finding:** Agent PRs add more lines and write significantly longer PR descriptions, but touch the same number of files as humans.

---

#### RQ2 — To what extent are Agent fix PRs rejected?

**Full sample:**
| Group | Merged | Total | Rate |
|---|---|---|---|
| Agent PRs | 38,464 | 48,624 | **79.1%** |
| Human PRs | 245,530 | 300,786 | **81.6%** |

Chi-squared test: **p < 0.0001** — statistically significant difference.

**Per-agent merge rates (full sample):**
| Agent | Merge Rate |
|---|---|
| Cursor | 87.7% |
| Claude Code | 84.5% |
| Copilot | 57.8% |
| Devin | 45.0% |

**Time to merge (full sample):**
- Agent PRs: **1.42 hours** (median)
- Human PRs: **5.43 hours** (median)

**Key finding:** Agents are merged faster than humans but at slightly lower rates overall. Cursor and Claude Code outperform humans; Copilot and Devin lag significantly.

---

#### RQ3 — What proportion of fix PRs are accepted without revisions?

**Full sample (single-commit = no revision):**
- Agent PRs: **48.9%** merged without revision
- Human PRs: **49.9%** merged without revision

For revised PRs (>1 commit), both groups had a median of **3 commits**.

Agent revision effort was higher:
- More lines added/deleted per revision cycle
- Agents modify ~79.6–120.5% of their initial code in revisions vs. 81–85% for humans

**Key finding:** Agents require roughly the same number of revision rounds as humans, but each revision tends to touch more code.

---

### 4. Acceptance Rate Trend (AIDev vs. Our Data)

| Period | AIDev Rate | Our Data Rate |
|---|---|---|
| Dec 2024 – Jul 2025 | 78.25% | 52.2% |
| Aug 2025 – Feb 2026 | — | 81.1% |

**Key finding:** Our data for the AIDev overlap period shows lower acceptance (52.2%), while the later months (Aug 2025 – Feb 2026) align with AIDev's ~78–81% rates. This suggests early-period quality gaps that later resolved.

---

## Key Overall Findings Summary

1. **Agent PRs are written faster and merged faster** than human PRs (1.4h vs 5.4h median).
2. **Agent merge rate is slightly lower** than human overall (79.1% vs 81.6%), but the gap is statistically significant.
3. **Cursor and Claude Code are top performers**, both exceeding human merge rates.
4. **Devin and Copilot underperform**, with Devin as low as 39.9–45% in some datasets.
5. **Agent PRs write more code and longer descriptions** but don't necessarily improve quality — revisions are heavier.
6. **Agent usage grew rapidly** — from ~1,900 PRs/month in Dec 2024 to ~5,000+ by mid-2025.
7. **~49% of agent PRs are merged without any revision**, comparable to humans (~50%).

---

## Next Steps

### Immediate
- [ ] Investigate **why Copilot and Devin have low merge rates** — are reviewers rejecting them for code quality, style, or task suitability?
- [ ] Analyze **PR rejection reasons** — closed-without-merge PRs: were they superseded, abandoned, or actively rejected?
- [ ] Compare **PR description quality** as a signal for acceptance likelihood.

### Analysis Extensions
- [ ] Run **per-repository analysis** — do certain repos accept agent PRs at higher rates? (See top repos in AIDev report)
- [ ] Investigate the **"revision spike"** — agent PRs modify 120.5% of initial code in revisions (own data). What drives this?
- [ ] Correlate **PR size with merge rate** — do smaller agent PRs get merged more often?
- [ ] Analyze **language/tech stack impact** — do agents perform better in JavaScript vs Python vs etc.?

### Dataset Extensions
- [ ] Extend collection beyond Feb 2026
- [ ] Add **code quality metrics** (test coverage, linting errors) to PRs
- [ ] Cross-reference with **issue trackers** to evaluate whether agent fixes actually resolve the reported bug

### Publication / Reporting
- [ ] Finalize figures from `results/` folders for paper inclusion
- [ ] Write comparison section between AIDev baseline and our full dataset
- [ ] Address the AIDev overlap discrepancy (52.2% vs 78.25%) in the paper

---

## File Reference

| File | Purpose |
|---|---|
| `collector.py` | Main data collection pipeline |
| `classify_fix_prs.py` | Fix PR classification via regex |
| `generate_report.ipynb` | Full-sample analysis notebook |
| `generate_report_matched.ipynb` | Matched-sample analysis notebook |
| `aidev_acceptance_rate.ipynb` | AIDev dataset acceptance rate analysis |
| `own_data_acceptance_rate.ipynb` | Our data (AIDev period) analysis |
| `rest_months_acceptance_rate.ipynb` | Aug 2025 – Feb 2026 analysis |
| `results/report.txt` | Full-sample statistical report |
| `results/matched_report.txt` | Matched-sample statistical report |
| `results/aidev_report.txt` | AIDev baseline report |
| `results/own_data_report.txt` | Own data report |
| `results/rest_months_report.txt` | Rest months report |
| `results/*/` | All generated figures (PNG) |
