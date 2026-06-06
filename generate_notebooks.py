#!/usr/bin/env python3
"""
generate_notebooks.py
Run once to create the three theme analysis notebooks.

Usage:
    python generate_notebooks.py
"""

import json
import uuid
from pathlib import Path


# ── Notebook helpers ──────────────────────────────────────────────────────────

def c_md(text: str) -> dict:
    return {"cell_type": "markdown", "id": uuid.uuid4().hex[:8],
            "metadata": {}, "source": text}


def c_code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "id": uuid.uuid4().hex[:8],
            "metadata": {}, "outputs": [], "source": text}


def notebook(cells: list) -> dict:
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.8.0"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def save_nb(nb: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
    print(f"  Created: {path}")


# ── Shared cells used in every notebook ───────────────────────────────────────

INSTALL = c_code("%pip install matplotlib seaborn scipy pyarrow fsspec requests")

IMPORTS = c_code(
    "import sys\n"
    "sys.path.insert(0, '.')\n"
    "from analysis_utils import (\n"
    "    load_fix_prs, load_commits, load_commit_details, build_revision_stats,\n"
    "    merge_rate, chi_square, mann_whitney, sig_label,\n"
    "    set_plot_style, save_fig,\n"
    "    AGENTS, AGENT_COLORS, THEME1_DIR, THEME2_DIR, THEME3_DIR,\n"
    ")\n"
    "import pandas as pd\n"
    "import matplotlib.pyplot as plt\n"
    "import warnings\n"
    "warnings.filterwarnings('ignore')\n"
    "%matplotlib inline\n"
    "set_plot_style()"
)


# ══════════════════════════════════════════════════════════════════════════════
# THEME 1 — Bug-Fix Adoption Trends
# RQ1: How does AI bug-fixing volume change over time?
# RQ7: Do developers switch agents for bug fixing over time?
# ══════════════════════════════════════════════════════════════════════════════

T1_CELLS = [
    c_md(
        "# Theme 1: Bug-Fix Adoption Trends\n\n"
        "**RQ1:** How does AI bug-fixing *volume* change over time?  \n"
        "**RQ7:** Do developers *switch agents* for bug fixing over time?\n\n"
        "Dataset: `mabujadallah/GitHub-Agentic-PR-Dataset`  \n"
        "Coverage: Dec 2024 – Feb 2026 (15 months)  \n"
        "Scope: closed bug-fix PRs only (Copilot, Cursor, Claude Code, Devin + Human baseline)"
    ),
    INSTALL,
    IMPORTS,
    c_code(
        "# ── Load data ────────────────────────────────────────────────────────\n"
        "df         = load_fix_prs()\n"
        "agents_df  = df[df['is_agent'] & df['agent'].isin(AGENTS)].copy()\n"
        "human_df   = df[~df['is_agent']].copy()\n"
        "print('Agent fix PRs:', f\"{len(agents_df):,}\")\n"
        "print('Human fix PRs:', f\"{len(human_df):,}\")"
    ),

    # ── RQ1 ──────────────────────────────────────────────────────────────────
    c_md("## RQ1: How does AI bug-fixing volume change over time?"),

    c_code(
        "# Monthly volume: agent (all 4) vs human\n"
        "monthly = df.groupby(['month', 'is_agent']).size().unstack(fill_value=0)\n"
        "monthly.columns = ['Human', 'Agent']\n"
        "monthly.index   = monthly.index.astype(str)\n"
        "monthly['Total']    = monthly['Agent'] + monthly['Human']\n"
        "monthly['Agent_%']  = (monthly['Agent'] / monthly['Total'] * 100).round(1)\n"
        "print('Monthly volume (Agent vs Human):')\n"
        "print(monthly.to_string())"
    ),

    c_code(
        "# Figure: monthly volume — agent vs human (line chart)\n"
        "fig, ax = plt.subplots(figsize=(12, 4))\n"
        "ax.plot(monthly.index, monthly['Agent'], 'o-', color='#1f77b4', label='Agent (all)')\n"
        "ax.plot(monthly.index, monthly['Human'], 's--', color='#7f7f7f', label='Human', alpha=0.7)\n"
        "ax.axvline('2025-07', color='red', linestyle=':', linewidth=1.5, label='AIDev cutoff (Jul-25)')\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Bug-Fix PRs')\n"
        "ax.set_title('RQ1: Monthly Bug-Fix PR Volume — Agent vs Human')\n"
        "ax.legend()\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq1_volume_agent_vs_human', THEME1_DIR)"
    ),

    c_code(
        "# Monthly volume per agent\n"
        "per_agent = agents_df.groupby(['month', 'agent']).size().unstack(fill_value=0)\n"
        "per_agent.index = per_agent.index.astype(str)\n"
        "cols = [a for a in AGENTS if a in per_agent.columns]\n"
        "per_agent = per_agent[cols]\n"
        "print('Monthly volume by agent:')\n"
        "print(per_agent.to_string())"
    ),

    c_code(
        "# Figure: per-agent stacked bar (monthly volume)\n"
        "fig, ax = plt.subplots(figsize=(13, 5))\n"
        "colors = [AGENT_COLORS[a] for a in per_agent.columns]\n"
        "per_agent.plot(kind='bar', stacked=True, ax=ax, color=colors, width=0.8)\n"
        "ax.axvline(\n"
        "    per_agent.index.tolist().index('2025-07') + 0.5,\n"
        "    color='red', linestyle=':', linewidth=1.5, label='AIDev cutoff'\n"
        ")\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Bug-Fix PRs')\n"
        "ax.set_title('RQ1: Monthly Bug-Fix Volume by Agent')\n"
        "ax.legend(loc='upper left')\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq1_volume_by_agent', THEME1_DIR)"
    ),

    # ── RQ7 ──────────────────────────────────────────────────────────────────
    c_md(
        "## RQ7: Do developers switch agents for bug fixing over time?\n\n"
        "Measured as each agent's **share of total agent bug-fix PRs** per month."
    ),

    c_code(
        "# Agent market share: % of agent bug-fix PRs from each tool per month\n"
        "share = per_agent.div(per_agent.sum(axis=1), axis=0) * 100\n"
        "print('Agent market share (%) per month:')\n"
        "print(share.round(1).to_string())"
    ),

    c_code(
        "# Figure: market share line chart\n"
        "fig, ax = plt.subplots(figsize=(13, 5))\n"
        "for agent in share.columns:\n"
        "    ax.plot(share.index, share[agent], 'o-',\n"
        "            color=AGENT_COLORS[agent], label=agent, linewidth=2)\n"
        "ax.axvline('2025-07', color='red', linestyle=':', linewidth=1.5, label='AIDev cutoff')\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Share of Agent Bug-Fix PRs (%)')\n"
        "ax.set_title('RQ7: Agent Market Share in Bug Fixing Over Time')\n"
        "ax.legend()\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq7_agent_market_share', THEME1_DIR)"
    ),

    c_code(
        "# Figure: stacked 100% bar — proportional agent usage\n"
        "fig, ax = plt.subplots(figsize=(13, 5))\n"
        "colors = [AGENT_COLORS[a] for a in share.columns]\n"
        "share.plot(kind='bar', stacked=True, ax=ax, color=colors, width=0.8)\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Share (%)')\n"
        "ax.set_ylim(0, 100)\n"
        "ax.set_title('RQ7: Proportional Agent Usage for Bug Fixing (Monthly)')\n"
        "ax.legend(loc='upper right')\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq7_agent_share_stacked', THEME1_DIR)"
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# THEME 2 — Bug-Fix Quality Over Time
# RQ2: Acceptance rate over time
# RQ3: Time to merge over time
# RQ4: Patch size over time
# RQ5: Revision burden over time
# ══════════════════════════════════════════════════════════════════════════════

T2_CELLS = [
    c_md(
        "# Theme 2: Bug-Fix Quality Over Time\n\n"
        "**RQ2:** How does bug-fix *acceptance rate* change over time?  \n"
        "**RQ3:** How does *time to merge* change over time?  \n"
        "**RQ4:** How does *patch size* change over time?  \n"
        "**RQ5:** How does *revision burden* change over time?\n\n"
        "Dataset: `mabujadallah/GitHub-Agentic-PR-Dataset`  \n"
        "Coverage: Dec 2024 – Feb 2026 (15 months)"
    ),
    INSTALL,
    IMPORTS,
    c_code(
        "# ── Load data ────────────────────────────────────────────────────────\n"
        "df         = load_fix_prs()\n"
        "agents_df  = df[df['is_agent'] & df['agent'].isin(AGENTS)].copy()\n"
        "human_df   = df[~df['is_agent']].copy()\n"
        "\n"
        "# Commit data needed for RQ5\n"
        "commits   = load_commits()\n"
        "details   = load_commit_details()\n"
        "rev_stats = build_revision_stats(df, commits, details)\n"
        "print('All data loaded.')"
    ),

    # ── RQ2 ──────────────────────────────────────────────────────────────────
    c_md("## RQ2: How does bug-fix acceptance rate change over time?"),

    c_code(
        "# Monthly merge rate per agent + human baseline\n"
        "months = sorted(df['month'].unique())\n"
        "rows   = []\n"
        "for m in months:\n"
        "    row = {'month': str(m)}\n"
        "    for agent in AGENTS:\n"
        "        sub = agents_df[(agents_df['month'] == m) & (agents_df['agent'] == agent)]\n"
        "        row[agent] = round(sub['is_merged'].mean() * 100, 1) if len(sub) >= 5 else None\n"
        "    h = human_df[human_df['month'] == m]\n"
        "    row['Human'] = round(h['is_merged'].mean() * 100, 1) if len(h) >= 5 else None\n"
        "    rows.append(row)\n"
        "monthly_rate = pd.DataFrame(rows).set_index('month')\n"
        "print('Monthly merge rate (%):')\n"
        "print(monthly_rate.to_string())"
    ),

    c_code(
        "# Figure: monthly acceptance rate per agent vs human\n"
        "fig, ax = plt.subplots(figsize=(13, 5))\n"
        "for agent in AGENTS:\n"
        "    ax.plot(monthly_rate.index, monthly_rate[agent], 'o-',\n"
        "            color=AGENT_COLORS[agent], label=agent, linewidth=1.8)\n"
        "ax.plot(monthly_rate.index, monthly_rate['Human'], 's--',\n"
        "        color=AGENT_COLORS['Human'], linewidth=2.5, label='Human', zorder=5)\n"
        "ax.axvline('2025-07', color='red', linestyle=':', linewidth=1.5, label='AIDev cutoff')\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Merge Rate (%)')\n"
        "ax.set_ylim(0, 105)\n"
        "ax.set_title('RQ2: Monthly Bug-Fix Acceptance Rate per Agent vs Human')\n"
        "ax.legend()\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq2_monthly_merge_rate', THEME2_DIR)"
    ),

    # ── RQ3 ──────────────────────────────────────────────────────────────────
    c_md("## RQ3: How does time to merge change over time?"),

    c_code(
        "# Monthly median time-to-merge per agent + human\n"
        "merged_agents = agents_df[agents_df['is_merged']]\n"
        "merged_human  = human_df[human_df['is_merged']]\n"
        "rows_ttm = []\n"
        "for m in months:\n"
        "    row = {'month': str(m)}\n"
        "    for agent in AGENTS:\n"
        "        sub = merged_agents[(merged_agents['month'] == m) & (merged_agents['agent'] == agent)]\n"
        "        row[agent] = round(sub['hours_to_merge'].median(), 2) if len(sub) >= 5 else None\n"
        "    h = merged_human[merged_human['month'] == m]\n"
        "    row['Human'] = round(h['hours_to_merge'].median(), 2) if len(h) >= 5 else None\n"
        "    rows_ttm.append(row)\n"
        "monthly_ttm = pd.DataFrame(rows_ttm).set_index('month')\n"
        "print('Monthly median time to merge (hours):')\n"
        "print(monthly_ttm.to_string())"
    ),

    c_code(
        "# Figure: monthly time to merge\n"
        "fig, ax = plt.subplots(figsize=(13, 5))\n"
        "for agent in AGENTS:\n"
        "    ax.plot(monthly_ttm.index, monthly_ttm[agent], 'o-',\n"
        "            color=AGENT_COLORS[agent], label=agent, linewidth=1.8)\n"
        "ax.plot(monthly_ttm.index, monthly_ttm['Human'], 's--',\n"
        "        color=AGENT_COLORS['Human'], linewidth=2.5, label='Human', zorder=5)\n"
        "ax.axvline('2025-07', color='red', linestyle=':', linewidth=1.5, label='AIDev cutoff')\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Median Time to Merge (hours)')\n"
        "ax.set_title('RQ3: Monthly Median Time to Merge per Agent vs Human')\n"
        "ax.legend()\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq3_monthly_time_to_merge', THEME2_DIR)"
    ),

    # ── RQ4 ──────────────────────────────────────────────────────────────────
    c_md("## RQ4: How does bug-fix patch size change over time?"),

    c_code(
        "# Aggregate total lines added/deleted per PR\n"
        "pr_size = (\n"
        "    details.groupby('pr_id')\n"
        "    .agg(lines_added=('additions', 'sum'), lines_deleted=('deletions', 'sum'))\n"
        "    .reset_index()\n"
        "    .rename(columns={'pr_id': 'id'})\n"
        ")\n"
        "df_size       = df.merge(pr_size, on='id', how='left')\n"
        "agents_size   = df_size[df_size['is_agent'] & df_size['agent'].isin(AGENTS)]\n"
        "human_size    = df_size[~df_size['is_agent']]\n"
        "\n"
        "rows_size = []\n"
        "for m in months:\n"
        "    row = {'month': str(m)}\n"
        "    for agent in AGENTS:\n"
        "        sub = agents_size[(agents_size['month'] == m) & (agents_size['agent'] == agent)]\n"
        "        row[agent] = round(sub['lines_added'].median(), 1) if len(sub) >= 5 else None\n"
        "    h = human_size[human_size['month'] == m]\n"
        "    row['Human'] = round(h['lines_added'].median(), 1) if len(h) >= 5 else None\n"
        "    rows_size.append(row)\n"
        "monthly_size = pd.DataFrame(rows_size).set_index('month')\n"
        "print('Monthly median lines added:')\n"
        "print(monthly_size.to_string())"
    ),

    c_code(
        "# Figure: monthly patch size (lines added)\n"
        "fig, ax = plt.subplots(figsize=(13, 5))\n"
        "for agent in AGENTS:\n"
        "    ax.plot(monthly_size.index, monthly_size[agent], 'o-',\n"
        "            color=AGENT_COLORS[agent], label=agent, linewidth=1.8)\n"
        "ax.plot(monthly_size.index, monthly_size['Human'], 's--',\n"
        "        color=AGENT_COLORS['Human'], linewidth=2.5, label='Human', zorder=5)\n"
        "ax.axvline('2025-07', color='red', linestyle=':', linewidth=1.5, label='AIDev cutoff')\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Median Lines Added')\n"
        "ax.set_title('RQ4: Monthly Median Patch Size (Lines Added) per Agent vs Human')\n"
        "ax.legend()\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq4_monthly_patch_size', THEME2_DIR)"
    ),

    # ── RQ5 ──────────────────────────────────────────────────────────────────
    c_md("## RQ5: How does revision burden change over time?"),

    c_code(
        "# Monthly revision rate: % of merged PRs that had >1 commit\n"
        "agent_rev = rev_stats[rev_stats['agent'].isin(AGENTS)].copy()\n"
        "agent_rev['is_revised'] = agent_rev['num_commits'] > 1\n"
        "\n"
        "rows_rev = []\n"
        "for m in months:\n"
        "    row = {'month': str(m)}\n"
        "    for agent in AGENTS:\n"
        "        sub = agent_rev[(agent_rev['month'] == m) & (agent_rev['agent'] == agent)]\n"
        "        row[agent] = round(sub['is_revised'].mean() * 100, 1) if len(sub) >= 5 else None\n"
        "    rows_rev.append(row)\n"
        "monthly_rev = pd.DataFrame(rows_rev).set_index('month')\n"
        "print('Monthly revision rate (%):')\n"
        "print(monthly_rev.to_string())"
    ),

    c_code(
        "# Figure: monthly revision rate\n"
        "fig, ax = plt.subplots(figsize=(13, 5))\n"
        "for agent in AGENTS:\n"
        "    ax.plot(monthly_rev.index, monthly_rev[agent], 'o-',\n"
        "            color=AGENT_COLORS[agent], label=agent, linewidth=1.8)\n"
        "ax.axvline('2025-07', color='red', linestyle=':', linewidth=1.5, label='AIDev cutoff')\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Revision Rate (%)')\n"
        "ax.set_title('RQ5: Monthly Revision Rate per Agent')\n"
        "ax.legend()\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq5_monthly_revision_rate', THEME2_DIR)"
    ),

    c_code(
        "# Monthly median revision lines added (for revised PRs only)\n"
        "rows_revsize = []\n"
        "for m in months:\n"
        "    row = {'month': str(m)}\n"
        "    for agent in AGENTS:\n"
        "        sub = agent_rev[(agent_rev['month'] == m) & (agent_rev['agent'] == agent)\n"
        "                        & (agent_rev['num_commits'] > 1)]\n"
        "        row[agent] = round(sub['rev_lines_added'].median(), 1) if len(sub) >= 5 else None\n"
        "    rows_revsize.append(row)\n"
        "monthly_revsize = pd.DataFrame(rows_revsize).set_index('month')\n"
        "\n"
        "fig, ax = plt.subplots(figsize=(13, 5))\n"
        "for agent in AGENTS:\n"
        "    ax.plot(monthly_revsize.index, monthly_revsize[agent], 'o-',\n"
        "            color=AGENT_COLORS[agent], label=agent, linewidth=1.8)\n"
        "ax.axvline('2025-07', color='red', linestyle=':', linewidth=1.5, label='AIDev cutoff')\n"
        "ax.set_xlabel('Month')\n"
        "ax.set_ylabel('Median Revision Lines Added')\n"
        "ax.set_title('RQ5: Monthly Median Revision Effort (Lines Added in Revisions)')\n"
        "ax.legend()\n"
        "plt.xticks(rotation=45, ha='right')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq5_monthly_revision_effort', THEME2_DIR)"
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# THEME 3 — Agent Comparison & Temporal Benchmarking
# RQ6: Which agent is best at bug fixing?
# RQ8: AIDev period vs Post-AIDev period
# ══════════════════════════════════════════════════════════════════════════════

T3_CELLS = [
    c_md(
        "# Theme 3: Agent Comparison & Temporal Benchmarking\n\n"
        "**RQ6:** Which AI agent is *best at bug fixing*?  \n"
        "**RQ8:** How does performance compare *before and after the AIDev cutoff*?\n\n"
        "Dataset: `mabujadallah/GitHub-Agentic-PR-Dataset`  \n"
        "Coverage: Dec 2024 – Feb 2026 (15 months)  \n"
        "AIDev period: Dec 2024 – Jul 2025 | Post-AIDev: Aug 2025 – Feb 2026"
    ),
    INSTALL,
    IMPORTS,
    c_code(
        "# ── Load data ────────────────────────────────────────────────────────\n"
        "df         = load_fix_prs()\n"
        "agents_df  = df[df['is_agent'] & df['agent'].isin(AGENTS)].copy()\n"
        "human_df   = df[~df['is_agent']].copy()\n"
        "commits    = load_commits()\n"
        "details    = load_commit_details()\n"
        "rev_stats  = build_revision_stats(df, commits, details)\n"
        "agent_rev  = rev_stats[rev_stats['agent'].isin(AGENTS)].copy()\n"
        "human_rev  = rev_stats[~rev_stats['is_agent']].copy()\n"
        "print('All data loaded.')"
    ),

    # ── RQ6 ──────────────────────────────────────────────────────────────────
    c_md(
        "## RQ6: Which AI agent is best at bug fixing?\n\n"
        "Metrics: merge rate, time to merge, revision rate, revision effort.  \n"
        "Statistical tests vs Human baseline: chi-square (merge rate), Mann-Whitney U (continuous)."
    ),

    c_code(
        "# Overall merge rate — all agents vs human\n"
        "h_m, h_t, h_r = merge_rate(human_df)\n"
        "print(\"{:<15} {:>8} {:>8} {:>8}  {}\".format('Group', 'Merged', 'Total', 'Rate%', 'vs Human'))\n"
        "print('-' * 55)\n"
        "print(\"{:<15} {:>8,} {:>8,} {:>7.1f}%\".format('Human', h_m, h_t, h_r))\n"
        "for agent in AGENTS:\n"
        "    sub = agents_df[agents_df['agent'] == agent]\n"
        "    a_m, a_t, a_r = merge_rate(sub)\n"
        "    chi2, p = chi_square(a_m, a_t, h_m, h_t)\n"
        "    print(\"{:<15} {:>8,} {:>8,} {:>7.1f}%  chi2={:.1f} p={:.4f} {}\".format(\n"
        "        agent, a_m, a_t, a_r, chi2, p, sig_label(p)))"
    ),

    c_code(
        "# Figure: merge rate bar chart\n"
        "groups = AGENTS + ['Human']\n"
        "rates  = []\n"
        "for g in groups:\n"
        "    if g == 'Human':\n"
        "        _, _, r = merge_rate(human_df)\n"
        "    else:\n"
        "        _, _, r = merge_rate(agents_df[agents_df['agent'] == g])\n"
        "    rates.append(r)\n"
        "\n"
        "colors = [AGENT_COLORS[g] for g in groups]\n"
        "fig, ax = plt.subplots(figsize=(8, 5))\n"
        "bars = ax.bar(groups, rates, color=colors, edgecolor='white', linewidth=0.5)\n"
        "for bar, val in zip(bars, rates):\n"
        "    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,\n"
        "            f'{val:.1f}%', ha='center', fontsize=10)\n"
        "ax.set_ylabel('Merge Rate (%)')\n"
        "ax.set_ylim(0, 105)\n"
        "ax.set_title('RQ6: Bug-Fix Merge Rate by Agent vs Human')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq6_merge_rate', THEME3_DIR)"
    ),

    c_code(
        "# Time to merge — median + Mann-Whitney vs Human\n"
        "h_ttm = human_df.loc[human_df['is_merged'], 'hours_to_merge']\n"
        "print(\"{:<15} {:>16}  Mann-Whitney vs Human\".format('Group', 'Median TTM (h)'))\n"
        "print('-' * 55)\n"
        "print(\"{:<15} {:>16.2f}\".format('Human', h_ttm.median()))\n"
        "for agent in AGENTS:\n"
        "    sub = agents_df[(agents_df['agent'] == agent) & agents_df['is_merged']]\n"
        "    u, p = mann_whitney(sub['hours_to_merge'], h_ttm)\n"
        "    print(\"{:<15} {:>16.2f}  U={:.0f} p={:.4f} {}\".format(\n"
        "        agent, sub['hours_to_merge'].median(), u, p, sig_label(p)))"
    ),

    c_code(
        "# Figure: time to merge — boxplot (capped at 48h for readability)\n"
        "CAP = 48\n"
        "plot_data = []\n"
        "labels    = []\n"
        "for agent in AGENTS:\n"
        "    sub = agents_df[(agents_df['agent'] == agent) & agents_df['is_merged']]\n"
        "    plot_data.append(sub['hours_to_merge'].clip(upper=CAP).dropna())\n"
        "    labels.append(agent)\n"
        "plot_data.append(h_ttm.clip(upper=CAP).dropna())\n"
        "labels.append('Human')\n"
        "\n"
        "fig, ax = plt.subplots(figsize=(9, 5))\n"
        "bp = ax.boxplot(plot_data, labels=labels, patch_artist=True, notch=False)\n"
        "for patch, label in zip(bp['boxes'], labels):\n"
        "    patch.set_facecolor(AGENT_COLORS[label])\n"
        "    patch.set_alpha(0.75)\n"
        "ax.set_ylabel(f'Hours to Merge (capped at {CAP}h)')\n"
        "ax.set_title('RQ6: Time to Merge Distribution per Agent vs Human')\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq6_time_to_merge', THEME3_DIR)"
    ),

    c_code(
        "# Revision burden summary: % revised + median revision lines\n"
        "h_rev_pct = (human_rev['num_commits'] > 1).mean() * 100\n"
        "h_rev_add = human_rev.loc[human_rev['num_commits'] > 1, 'rev_lines_added'].median()\n"
        "h_rev_del = human_rev.loc[human_rev['num_commits'] > 1, 'rev_lines_deleted'].median()\n"
        "\n"
        "print(\"{:<15} {:>10} {:>16} {:>16}\".format(\n"
        "    'Group', '% Revised', 'Med RevLines+', 'Med RevLines-'))\n"
        "print('-' * 60)\n"
        "print(\"{:<15} {:>9.1f}% {:>16.0f} {:>16.0f}\".format(\n"
        "    'Human', h_rev_pct, h_rev_add, h_rev_del))\n"
        "for agent in AGENTS:\n"
        "    sub     = agent_rev[agent_rev['agent'] == agent]\n"
        "    pct     = (sub['num_commits'] > 1).mean() * 100\n"
        "    revised = sub[sub['num_commits'] > 1]\n"
        "    add     = revised['rev_lines_added'].median()\n"
        "    dlt     = revised['rev_lines_deleted'].median()\n"
        "    u_add, p_add = mann_whitney(\n"
        "        revised['rev_lines_added'],\n"
        "        human_rev.loc[human_rev['num_commits'] > 1, 'rev_lines_added']\n"
        "    )\n"
        "    print(\"{:<15} {:>9.1f}% {:>16.0f} {:>16.0f}  p(rev_lines)={:.4f} {}\".format(\n"
        "        agent, pct, add, dlt, p_add, sig_label(p_add)))"
    ),

    c_code(
        "# Figure: revision metrics bar chart (4 subplots)\n"
        "metrics   = ['Merge Rate %', 'Median TTM (h)', '% Revised', 'Med Rev Lines+']\n"
        "groups    = AGENTS + ['Human']\n"
        "all_vals  = {g: [] for g in groups}\n"
        "\n"
        "for g in groups:\n"
        "    sub = agents_df[agents_df['agent'] == g] if g != 'Human' else human_df\n"
        "    _, _, mr = merge_rate(sub)\n"
        "    ttm = sub.loc[sub['is_merged'], 'hours_to_merge'].median()\n"
        "    rev_sub = agent_rev[agent_rev['agent'] == g] if g != 'Human' else human_rev\n"
        "    rev_pct = (rev_sub['num_commits'] > 1).mean() * 100\n"
        "    rev_add = rev_sub.loc[rev_sub['num_commits'] > 1, 'rev_lines_added'].median()\n"
        "    all_vals[g] = [mr, ttm, rev_pct, rev_add]\n"
        "\n"
        "fig, axes = plt.subplots(1, 4, figsize=(16, 5))\n"
        "for i, (ax, metric) in enumerate(zip(axes, metrics)):\n"
        "    vals   = [all_vals[g][i] for g in groups]\n"
        "    colors = [AGENT_COLORS[g] for g in groups]\n"
        "    ax.bar(groups, vals, color=colors, edgecolor='white')\n"
        "    ax.set_title(metric, fontsize=10)\n"
        "    ax.set_xticklabels(groups, rotation=30, ha='right', fontsize=8)\n"
        "fig.suptitle('RQ6: Agent Comparison — Key Bug-Fix Metrics', fontsize=12)\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq6_summary', THEME3_DIR)"
    ),

    # ── RQ8 ──────────────────────────────────────────────────────────────────
    c_md(
        "## RQ8: AIDev period vs Post-AIDev period\n\n"
        "Does agent bug-fixing performance change after the AIDev coverage ends?\n\n"
        "- **AIDev period:** Dec 2024 – Jul 2025  \n"
        "- **Post-AIDev period:** Aug 2025 – Feb 2026"
    ),

    c_code(
        "# Merge rate per agent — AIDev vs Post-AIDev\n"
        "PERIODS = ['AIDev (Dec24-Jul25)', 'Post-AIDev (Aug25-Feb26)']\n"
        "rows_rq8 = []\n"
        "for agent in AGENTS:\n"
        "    sub = agents_df[agents_df['agent'] == agent]\n"
        "    for period in PERIODS:\n"
        "        p_sub = sub[sub['period'] == period]\n"
        "        m, t, r = merge_rate(p_sub)\n"
        "        rows_rq8.append({'Agent': agent, 'Period': period,\n"
        "                         'Merged': m, 'Total': t, 'Rate': r})\n"
        "\n"
        "rq8_df = pd.DataFrame(rows_rq8)\n"
        "pivot  = rq8_df.pivot(index='Agent', columns='Period', values='Rate').round(1)\n"
        "pivot.columns.name = None\n"
        "pivot['Change (pp)'] = (\n"
        "    pivot['Post-AIDev (Aug25-Feb26)'] - pivot['AIDev (Dec24-Jul25)']\n"
        ").round(1)\n"
        "print('Merge rate (%) — AIDev vs Post-AIDev:')\n"
        "print(pivot.to_string())"
    ),

    c_code(
        "# Statistical test: is the period change significant per agent?\n"
        "print(\"{:<15} {:>10} {:>10} {:>8} {:>10} {}\".format(\n"
        "    'Agent', 'AIDev %', 'Post %', 'chi2', 'p', 'sig'))\n"
        "print('-' * 65)\n"
        "for agent in AGENTS:\n"
        "    sub  = agents_df[agents_df['agent'] == agent]\n"
        "    ai   = sub[sub['period'] == 'AIDev (Dec24-Jul25)']\n"
        "    post = sub[sub['period'] == 'Post-AIDev (Aug25-Feb26)']\n"
        "    if len(ai) < 5 or len(post) < 5:\n"
        "        continue\n"
        "    ai_m, ai_t, ai_r   = merge_rate(ai)\n"
        "    po_m, po_t, po_r   = merge_rate(post)\n"
        "    chi2, p            = chi_square(ai_m, ai_t, po_m, po_t)\n"
        "    print(\"{:<15} {:>9.1f}% {:>9.1f}% {:>8.2f} {:>10.4f} {}\".format(\n"
        "        agent, ai_r, po_r, chi2, p, sig_label(p)))"
    ),

    c_code(
        "# Figure: grouped bar — AIDev vs Post-AIDev per agent\n"
        "x     = list(range(len(AGENTS)))\n"
        "width = 0.35\n"
        "\n"
        "fig, ax = plt.subplots(figsize=(9, 5))\n"
        "for i, period in enumerate(PERIODS):\n"
        "    vals   = [rq8_df[(rq8_df['Agent'] == a) & (rq8_df['Period'] == period)]['Rate'].values[0]\n"
        "              for a in AGENTS]\n"
        "    offset = (i - 0.5) * width\n"
        "    bars   = ax.bar([xi + offset for xi in x], vals, width,\n"
        "                    label=period, alpha=0.85)\n"
        "    for bar, v in zip(bars, vals):\n"
        "        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,\n"
        "                f'{v:.1f}%', ha='center', fontsize=8)\n"
        "\n"
        "ax.set_xticks(x)\n"
        "ax.set_xticklabels(AGENTS)\n"
        "ax.set_ylabel('Merge Rate (%)')\n"
        "ax.set_ylim(0, 105)\n"
        "ax.set_title('RQ8: Bug-Fix Acceptance Rate — AIDev vs Post-AIDev Period')\n"
        "ax.legend()\n"
        "fig.tight_layout()\n"
        "save_fig(fig, 'rq8_aidev_vs_post_aidev', THEME3_DIR)"
    ),

    c_code(
        "# Time to merge: AIDev vs Post-AIDev per agent\n"
        "print(\"{:<15} {:>14} {:>14}  Mann-Whitney\".format(\n"
        "    'Agent', 'AIDev TTM (h)', 'Post TTM (h)'))\n"
        "print('-' * 60)\n"
        "for agent in AGENTS:\n"
        "    sub  = agents_df[(agents_df['agent'] == agent) & agents_df['is_merged']]\n"
        "    ai   = sub[sub['period'] == 'AIDev (Dec24-Jul25)']['hours_to_merge']\n"
        "    post = sub[sub['period'] == 'Post-AIDev (Aug25-Feb26)']['hours_to_merge']\n"
        "    if len(ai) < 5 or len(post) < 5:\n"
        "        continue\n"
        "    u, p = mann_whitney(ai, post)\n"
        "    print(\"{:<15} {:>14.2f} {:>14.2f}  U={:.0f} p={:.4f} {}\".format(\n"
        "        agent, ai.median(), post.median(), u, p, sig_label(p)))"
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# Save all notebooks
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Generating notebooks ...")
    save_nb(notebook(T1_CELLS), "theme1_adoption_trends.ipynb")
    save_nb(notebook(T2_CELLS), "theme2_quality_over_time.ipynb")
    save_nb(notebook(T3_CELLS), "theme3_agent_comparison.ipynb")
    print("\nDone. Run each notebook in Jupyter to produce figures and tables.")
