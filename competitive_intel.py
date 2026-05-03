#!/usr/bin/env python3
"""
Weekly Competitive Intelligence Agent

Two-phase approach:
  1. Research phase  — web search to gather raw findings for all competitors
  2. Curation phase  — selects the top 5 items by importance × uniqueness × freshness
                       (compares against last week's top 5 stored in ci_state.json)

Requirements:
    pip install anthropic pydantic python-dotenv

Environment variables (.env file or shell exports):
    ANTHROPIC_API_KEY   — your Anthropic API key
    EMAIL_TO            — recipient email address
    SMTP_HOST           — SMTP server hostname (default: smtp.gmail.com)
    SMTP_PORT           — SMTP port (default: 587)
    SMTP_USER           — SMTP login address (also used as sender)
    SMTP_PASSWORD       — SMTP password or Gmail app password
                          (Gmail: https://myaccount.google.com/apppasswords)

Cron — every Monday at 8am:
    0 8 * * 1 cd /path/to/project && /path/to/venv/bin/python competitive_intel.py >> ci_agent.log 2>&1
"""

import datetime
import json
import os
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

DOCS_DATA_DIR = Path(__file__).parent / "docs" / "data"

import anthropic
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── COMPETITORS ───────────────────────────────────────────────────────────────
COMPETITORS = [
    "Anthropic",
    "OpenAI",
    "Databricks",
    "Google Vertex AI / BigQuery",
    "Microsoft Fabric / Azure ML",
    "Snowflake",
    "Palantir AIP",
]
# ─────────────────────────────────────────────────────────────────────────────

STATE_FILE = Path(__file__).parent / "ci_state.json"

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.environ.get("EMAIL_TO", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


# ── Pydantic models for structured curation output ───────────────────────────

class IntelItem(BaseModel):
    competitor: str
    title: str
    summary: str        # 2–3 sentences, actionable signal only
    why_it_matters: str # 1 sentence on strategic implication for SageMaker Unified Studio
    pm_summary: str     # 4–6 sentence PM-focused brief: what happened, key details, competitive implications, what to watch
    source_url: Optional[str] = None
    importance_score: int   # 1–10 internal ranking (not shown in email)

class CuratedReport(BaseModel):
    top5: list[IntelItem]   # exactly 5 items


# ── State management ──────────────────────────────────────────────────────────

def load_last_run_state() -> Optional[dict]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return None
    return None


def save_state(report_date: str, items: list[IntelItem]) -> None:
    state = {
        "date": report_date,
        "top5": [
            {
                "competitor": item.competitor,
                "title": item.title,
                "summary": item.summary,
            }
            for item in items
        ],
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))


def save_report_json(report: "CuratedReport", report_date: str) -> None:
    """Write the curated report as JSON for the web dashboard."""
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "date": report_date,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "competitors": COMPETITORS,
        "top5": [
            {
                "rank": i + 1,
                "competitor": item.competitor,
                "title": item.title,
                "summary": item.summary,
                "why_it_matters": item.why_it_matters,
                "pm_summary": item.pm_summary,
                "source_url": item.source_url,
                "importance_score": item.importance_score,
            }
            for i, item in enumerate(report.top5)
        ],
    }

    # Dated file for history
    slug = datetime.date.today().strftime("%Y-%m-%d")
    dated_path = DOCS_DATA_DIR / f"{slug}.json"
    dated_path.write_text(json.dumps(payload, indent=2))

    # Always update latest.json
    (DOCS_DATA_DIR / "latest.json").write_text(json.dumps(payload, indent=2))

    # Update index.json — list of all report dates for history navigation
    index_path = DOCS_DATA_DIR / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text())
    else:
        index = {"reports": []}

    if slug not in index["reports"]:
        index["reports"].insert(0, slug)  # newest first

    index_path.write_text(json.dumps(index, indent=2))
    print(f"✓ Report JSON saved to docs/data/{slug}.json")


# ── Phase 1: Research ─────────────────────────────────────────────────────────

def gather_raw_intelligence(client: anthropic.Anthropic) -> str:
    """
    Use web search to gather comprehensive raw findings for all competitors.
    Returns a detailed markdown text — NOT yet formatted for the email.
    Handles pause_turn (server-side tool loop continuation) automatically.
    """
    today = datetime.date.today().strftime("%B %d, %Y")
    competitors_block = "\n".join(f"  - {c}" for c in COMPETITORS)

    prompt = f"""Today is {today}. You are a thorough research assistant gathering competitive intelligence.

Use web search to research the following competitors from the past 7 days:

{competitors_block}

For each competitor, find and document:
1. **News & press releases** — funding rounds, partnerships, executive moves, major announcements
2. **Product updates** — new features, launches, blog posts about product changes, changelog entries, pricing changes

Also gather:
**BROADER AI/LLM INDUSTRY HIGHLIGHTS** — 5–8 interesting developments from the broader AI/ML community (academic labs, independent researchers, industry voices — not necessarily from the competitors above).

---

Return comprehensive raw findings in plain markdown. Be thorough — include everything potentially notable.
For each item include: what happened, when (approximate date if available), and a source URL where possible.
If a competitor has nothing notable this week, say so explicitly.
Do NOT filter or rank — capture everything; a separate curation step will select the best items."""

    messages: list[anthropic.types.MessageParam] = [
        {"role": "user", "content": prompt}
    ]

    max_continuations = 10
    for _ in range(max_continuations):
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            tools=[
                {"type": "web_search_20260209", "name": "web_search"},
            ],
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    return block.text
            break

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        break

    return ""


# ── Phase 2: Curation ─────────────────────────────────────────────────────────

def curate_top5(client: anthropic.Anthropic, raw_findings: str, last_state: Optional[dict]) -> CuratedReport:
    """
    Given the raw research findings, select the top 5 items optimising for:
      - Strategic importance / impact
      - Uniqueness across the 5 (no two items about the same thing)
      - Freshness vs. last week (deprioritise stories already highlighted)

    Uses structured output via client.messages.parse() to return typed JSON.
    """
    today = datetime.date.today().strftime("%B %d, %Y")

    # Truncate raw findings before embedding in prompt
    max_findings_chars = 12000
    if len(raw_findings) > max_findings_chars:
        raw_findings = raw_findings[:max_findings_chars] + "\n\n[findings truncated]"

    freshness_block = ""
    if last_state:
        prev_date = last_state.get("date", "last week")
        prev_items = last_state.get("top5", [])
        if prev_items:
            prev_bullets = "\n".join(
                f"  - [{item['competitor']}] {item['title']}: {item['summary']}"
                for item in prev_items
            )
            freshness_block = f"""
**PREVIOUS WEEK'S TOP 5 (from {prev_date}) — deprioritise these unless there is significant new development:**
{prev_bullets}
"""

    prompt = f"""Today is {today}. You are a senior competitive intelligence analyst for the Amazon SageMaker Unified Studio team.

Below are this week's raw research findings across our key competitors:

---
{raw_findings}
---
{freshness_block}

Your task: select exactly 5 items that a senior product leader should read this week.

Selection criteria (apply in order):
1. **Strategic importance** — material impact on our market position or roadmap decisions
2. **Uniqueness** — the 5 items should span different competitors and different themes (no two items about the same topic)
3. **Incremental freshness** — heavily deprioritise anything already covered in the previous week's top 5 unless there is a significant NEW development

For each selected item:
- competitor: exact competitor name from the list
- title: punchy 6–10 word headline
- summary: 2–3 sentences of factual detail, actionable signal only
- why_it_matters: 1 sentence on the strategic implication for Amazon SageMaker Unified Studio
- pm_summary: 3 sentence PM brief: (1) what happened and key details (features, pricing, dates), (2) what it signals about the competitor's strategy, (3) the specific threat or opportunity for Amazon SageMaker Unified Studio.
- source_url: URL if available, otherwise null
- importance_score: your internal 1–10 ranking (1 = lowest)

Return exactly 5 items, ordered by importance_score descending."""

    for attempt in range(5):
        try:
            response = client.messages.parse(
                model="claude-opus-4-7",
                max_tokens=2500,
                output_config={"effort": "medium"},
                messages=[{"role": "user", "content": prompt}],
                output_format=CuratedReport,
            )
            return response.parsed
        except anthropic.RateLimitError:
            wait = 60 * (attempt + 1)
            print(f"  Rate limit hit — waiting {wait}s before retry {attempt + 1}/5...")
            time.sleep(wait)

    raise RuntimeError("Curation failed after 5 retries due to rate limits")


# ── Email builder ─────────────────────────────────────────────────────────────

def build_email_html(report: CuratedReport, report_date: str) -> str:
    """Render the curated top-5 report as a polished inline-CSS HTML email."""

    competitors_pills = "".join(
        f'<span style="display:inline-block;background:#f3f4f6;border-radius:4px;'
        f'padding:2px 8px;margin:2px;font-size:12px;color:#374151;">{c}</span>'
        for c in COMPETITORS
    )

    rank_colors = ["#5046e5", "#7c3aed", "#0891b2", "#059669", "#d97706"]

    items_html = ""
    for i, item in enumerate(report.top5):
        rank_color = rank_colors[i] if i < len(rank_colors) else "#6b7280"
        source_link = (
            f'<a href="{item.source_url}" style="color:#5046e5;font-size:11px;'
            f'text-decoration:none;">↗ Source</a>'
            if item.source_url
            else ""
        )
        items_html += f"""
        <div style="border:1px solid #e5e7eb;border-radius:8px;padding:20px;margin-bottom:16px;">
          <div style="display:flex;align-items:center;margin-bottom:10px;">
            <span style="background:{rank_color};color:#fff;font-size:11px;font-weight:700;
                         border-radius:4px;padding:2px 8px;margin-right:10px;">#{i+1}</span>
            <span style="background:#f3f4f6;color:#374151;font-size:11px;border-radius:4px;
                         padding:2px 8px;">{item.competitor}</span>
          </div>
          <h3 style="margin:0 0 8px;font-size:15px;color:#1a1a1a;font-weight:700;line-height:1.4;">{item.title}</h3>
          <p style="margin:0 0 10px;font-size:13px;color:#374151;line-height:1.6;">{item.summary}</p>
          <div style="background:#f9fafb;border-left:3px solid {rank_color};
                      padding:8px 12px;border-radius:0 4px 4px 0;margin-bottom:8px;">
            <p style="margin:0;font-size:12px;color:#4b5563;line-height:1.5;">
              <strong style="color:#1a1a1a;">Why it matters:</strong> {item.why_it_matters}
            </p>
          </div>
          {source_link}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.1);overflow:hidden;">

        <!-- Header -->
        <tr>
          <td style="background:#5046e5;padding:24px 32px;">
            <p style="margin:0;font-size:11px;color:#c7d2fe;letter-spacing:1px;text-transform:uppercase;">Weekly Briefing</p>
            <h1 style="margin:4px 0 0;font-size:22px;color:#ffffff;font-weight:700;">Competitive Intelligence</h1>
            <p style="margin:8px 0 0;font-size:13px;color:#c7d2fe;">{report_date} &middot; Top 5 this week</p>
          </td>
        </tr>

        <!-- Tracking pill row -->
        <tr>
          <td style="padding:12px 32px;background:#fafafa;border-bottom:1px solid #e5e7eb;">
            <p style="margin:0;font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">Tracking</p>
            {competitors_pills}
          </td>
        </tr>

        <!-- Report body -->
        <tr>
          <td style="padding:24px 32px;color:#374151;font-size:14px;line-height:1.7;">
            <p style="margin:0 0 20px;font-size:13px;color:#6b7280;">
              Curated from this week's news across {len(COMPETITORS)} competitors — ranked by strategic importance,
              optimised for variety and freshness vs. last week.
            </p>
            {items_html}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;text-align:center;">
            <p style="margin:0;font-size:11px;color:#9ca3af;">
              Generated by your Competitive Intelligence Agent &middot; {report_date}
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Email sender ──────────────────────────────────────────────────────────────

def send_email(html_content: str, report_date: str) -> None:
    subject = f"Competitive Intelligence — Top 5 — {report_date}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    plain = (
        f"Weekly Competitive Intelligence Report — {report_date}\n\n"
        "View this email in an HTML-capable client for the full formatted report.\n\n"
        f"Tracking: {', '.join(COMPETITORS)}"
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())

    print(f"✓ Report sent to {EMAIL_TO}")


# ── Config validation ─────────────────────────────────────────────────────────

def email_configured() -> bool:
    return bool(EMAIL_TO and SMTP_USER and SMTP_PASSWORD)


def validate_config() -> None:
    if not ANTHROPIC_API_KEY:
        print("Error: missing required environment variable: ANTHROPIC_API_KEY", file=sys.stderr)
        print("Create a .env file or export it in your shell. See .env.example for reference.", file=sys.stderr)
        sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    validate_config()

    now = datetime.datetime.now().isoformat(timespec="seconds")
    report_date = datetime.date.today().strftime("%B %d, %Y")

    print(f"[{now}] Starting competitive intelligence run...")
    print(f"  Competitors: {', '.join(COMPETITORS)}")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    last_state = load_last_run_state()
    if last_state:
        print(f"  Loaded previous run state from {last_state.get('date', 'unknown date')}")
    else:
        print("  No previous run state found — freshness filter disabled for this run")

    print("  Phase 1: Researching via web search (this takes 2–4 minutes)...")
    raw_findings = gather_raw_intelligence(client)
    if not raw_findings:
        print("Error: research phase returned no findings", file=sys.stderr)
        sys.exit(1)

    print("  Waiting 60s for rate limit window to reset...")
    time.sleep(60)

    print("  Phase 2: Curating top 5 items...")
    curated = curate_top5(client, raw_findings, last_state)

    print("  Saving state for next run's freshness filter...")
    save_state(report_date, curated.top5)

    print("  Saving report JSON for web dashboard...")
    save_report_json(curated, report_date)

    if email_configured():
        print("  Building email...")
        full_html = build_email_html(curated, report_date)
        print("  Sending email...")
        send_email(full_html, report_date)
    else:
        print("  Email not configured — skipping (set EMAIL_TO, SMTP_USER, SMTP_PASSWORD to enable)")

    print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] Done.")
    print("  Top 5 this week:")
    for i, item in enumerate(curated.top5, 1):
        print(f"    {i}. [{item.competitor}] {item.title} (score: {item.importance_score})")


if __name__ == "__main__":
    main()
