# Competitive Intelligence Agent

Weekly AI-powered briefing for Amazon SageMaker Unified Studio — tracks 7 competitors, curates the top 5 most important updates each week, and publishes them to a GitHub Pages dashboard.

## How it works

1. **Research** — Claude searches the web for news and product updates across all competitors
2. **Curation** — A second Claude pass selects the top 5 items ranked by importance × uniqueness × freshness vs. last week
3. **Publish** — Results are written to `docs/data/` and committed; GitHub Pages serves the dashboard

## Setup

### 1. Fork / create this repo on GitHub

### 2. Add your API key as a secret

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your key from [console.anthropic.com](https://console.anthropic.com) |
| `EMAIL_TO` | *(optional)* recipient address |
| `SMTP_HOST` | *(optional)* e.g. `smtp.gmail.com` |
| `SMTP_PORT` | *(optional)* e.g. `587` |
| `SMTP_USER` | *(optional)* your Gmail address |
| `SMTP_PASSWORD` | *(optional)* Gmail App Password |

### 3. Enable GitHub Pages

Go to **Settings → Pages** and set:
- Source: **Deploy from a branch**
- Branch: `main` / `docs` folder

Your dashboard will be live at `https://<your-username>.github.io/<repo-name>/`

### 4. Trigger the first run

Go to **Actions → Weekly Competitive Intelligence → Run workflow**

The agent will run (~3 min), commit `docs/data/latest.json`, and your dashboard will update automatically.

After that it runs every Monday at 8am UTC automatically.

## Competitors tracked

- Anthropic
- OpenAI
- Databricks
- Google Vertex AI / BigQuery
- Microsoft Fabric / Azure ML
- Snowflake
- Palantir AIP

To change the list, edit `COMPETITORS` in `competitive_intel.py`.

## Local development

```bash
pip install anthropic pydantic python-dotenv
cp .env.example .env   # fill in your values
python competitive_intel.py
```
