# 🧠 CodeSage PR Bot

> AI-powered GitHub Pull Request reviewer. When a developer opens or updates a PR, CodeSage fetches the diff, analyzes it with Groq LLM, and posts inline review comments + a scored summary — exactly like a senior engineer would.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🔍 **Auto PR Review** | Reviews every PR automatically when opened or updated |
| 💬 **Inline Comments** | Posts comments directly on specific diff lines with severity + fix |
| 📊 **PR Score** | Scores the PR 0–100 and gives an overall assessment |
| 🏷️ **Auto Labels** | Applies `looks-good`, `needs-work`, or `security-risk` label |
| 🤖 **@codesage Chat** | Reply `@codesage <question>` on any PR — get an instant AI answer |
| ✅ **One-click Fixes** | Suggestions posted as GitHub suggestion blocks — accept with one click |
| 📁 **Context-aware** | Fetches related files (imports, tests) for deeper, smarter reviews |
| 🔔 **Slack Alerts** | Sends Slack notifications for critical bugs and security risks |
| ⚙️ **Per-repo Config** | Customize behaviour per repo via `.codesage.yml` |
| 🌐 **Multilingual** | Review comments in English, Hindi, Spanish, French, German, Chinese |
| 🔒 **Secure** | HMAC-SHA256 webhook signature verification on every request |
| ⚡ **Async** | Returns 200 to GitHub in < 1s, processes in background |
| 🔁 **Idempotent** | Never double-reviews or double-replies |

---

## 🏗️ Architecture

```
GitHub PR Opened / Updated
         │
         ▼
FastAPI /webhook ──► Verify HMAC-SHA256 signature
         │
         ▼ (background task)
config_loader.py ──► Load .codesage.yml from repo
         │
         ▼
github_client.py ──► Fetch PR files + diffs
         │
         ▼
context_fetcher.py ──► Fetch related files (tests, imports)
         │
         ▼
analyzer.py ──► Orchestrate per-file reviews (async, max 3 concurrent)
         │
         ├── prompt_builder.py ──► Build structured LLM prompt + context
         ├── llm_engine.py     ──► Groq API (3 retries, exponential backoff)
         ├── parser.py         ──► Parse JSON → ReviewComment objects
         └── autofix.py        ──► Format fixes as GitHub suggestion blocks
         │
         ▼
github_client.py ──► Post inline comments + summary + label
         │
         ▼
slack_notifier.py ──► Send Slack alert (critical bugs / security risks)

GitHub Comment (@codesage mention)
         │
         ▼
webhook.py ──► Detect mention, deduplicate
         │
         ▼
chat_handler.py ──► Ask Groq, post reply on PR
```

---

## 📁 Project Structure

```
CodeSage/
│
├── app/
│   ├── webhook.py          # FastAPI server — receives GitHub events
│   ├── github_client.py    # All GitHub API interactions
│   ├── analyzer.py         # Main orchestrator
│   ├── prompt_builder.py   # Builds LLM prompts (multilingual)
│   ├── llm_engine.py       # Groq API calls with retry logic
│   ├── parser.py           # Parses LLM JSON → ReviewComment objects
│   ├── autofix.py          # One-click GitHub suggestion blocks
│   ├── chat_handler.py     # @codesage chat reply handler
│   ├── config_loader.py    # Reads .codesage.yml from repo
│   ├── context_fetcher.py  # Fetches related files for context
│   └── slack_notifier.py   # Slack webhook notifications
│
├── models/
│   └── review_model.py     # ReviewComment + PRReview dataclasses
│
├── tests/
│   ├── test_parser.py
│   └── test_analyzer.py
│
├── .codesage.yml           # Sample config (drop this in any repo)
├── .env                    # Your keys (never commit this)
├── .env.example            # Safe template
├── requirements.txt
├── main.py                 # Entry point
└── README.md
```

---

## 🚀 Quick Start

### 1. Clone and set up

```bash
git clone https://github.com/yourname/codesage-pr-bot
cd codesage-pr-bot
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) — free tier |
| `GITHUB_TOKEN` | [GitHub Settings → Tokens](https://github.com/settings/tokens) — `repo` scope |
| `WEBHOOK_SECRET` | Any random string — `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SLACK_WEBHOOK_URL` | [api.slack.com/messaging/webhooks](https://api.slack.com/messaging/webhooks) — optional |

### 3. Start the server

```bash
uvicorn main:app --port 8000
```

You should see:
```
GROQ_API_KEY   : ✓ set
GITHUB_TOKEN   : ✓ set
WEBHOOK_SECRET : ✓ set
```

### 4. Expose to GitHub (dev only)

```bash
ngrok http 8000
```

Copy the `https://xxxx.ngrok-free.app` URL.

### 5. Register the webhook on GitHub

Go to your repo → **Settings → Webhooks → Add webhook**:

| Field | Value |
|---|---|
| Payload URL | `https://xxxx.ngrok-free.app/webhook` |
| Content type | `application/json` |
| Secret | Same value as `WEBHOOK_SECRET` in `.env` |
| Events | **Pull requests** + **Issue comments** |

### 6. Open a PR and watch the magic 🎉

---

## 💬 Using @codesage Chat

On any PR, add a comment mentioning `@codesage`:

```
@codesage why is this function dangerous?
@codesage how do I fix the division by zero issue?
@codesage explain what this diff is doing
```

CodeSage will reply with a detailed AI-powered answer within seconds.

---

## ⚙️ Per-repo Configuration (.codesage.yml)

Drop a `.codesage.yml` file in the **root of any repo** to customize CodeSage's behaviour:

```yaml
# Files/paths to skip
ignore_files:
  - "*.md"
  - "migrations/*"
  - "*.lock"

# Max files to review per PR
max_files: 10

# Severity levels to post as inline comments
severity_filter:
  - critical
  - warning

# Fetch related files for deeper context (default: true)
context_aware: true

# Reply language: en, hi, es, fr, de, zh
language: en

# Slack notifications
slack_notify: true
slack_channel: "#code-reviews"

# Extra review instructions
# custom_instructions: "Focus on async/await correctness."
```

---

## 🔔 Slack Notifications

CodeSage sends Slack alerts for:

| Event | Message |
|---|---|
| 🚨 Security risk detected | Red alert with file + line details |
| 🔴 Critical bugs found | Orange alert with issue list + PR link |
| ⚠️ PR needs work | Summary with score |
| ✅ PR looks good | Green confirmation |
| 💬 @codesage question asked | Notification with the question |

---

## 📋 Example Review Output

**Inline comment with one-click fix:**
```
🔴 [CRITICAL] Division by zero

Variable `b` can be 0 here, causing a ZeroDivisionError at runtime.

💡 Suggestion:
```suggestion
    if b == 0:
        return None
    return a / b
```
```

**Summary comment:**

| | |
|---|---|
| **Score** | 🟡 53/100 |
| **Language** | Python |
| **Files Reviewed** | 1 |
| **Critical Bugs** | 1 |
| **Warnings** | 1 |
| **Suggestions** | 2 |

### 🔴 Critical Issues
- `testing.py` L2 — Division by zero

### 🟡 Warnings
- `testing.py` L5 — Hardcoded password

---

## 🧪 Running Tests

```bash
pytest tests/ -v
```

---

## 🔧 Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Web Framework | FastAPI |
| AI Model | Groq `llama-3.3-70b-versatile` |
| GitHub API | PyGithub |
| ASGI Server | Uvicorn |
| HTTP Client | httpx |
| Config Parsing | PyYAML |
| Dev Tunnel | ngrok |
| Notifications | Slack Incoming Webhooks |
| Deployment | Railway / Render |

---

## 🔑 Key Engineering Decisions

**Webhook signature verification** — Every request verified with HMAC-SHA256. Prevents anyone from spoofing GitHub events.

**Per-file analysis** — Files sent individually to the LLM. Keeps prompts focused; avoids context window limits on large PRs.

**Context-aware prompts** — Related files (imports, tests, `__init__.py`) are fetched and attached to the prompt so the LLM understands the full codebase context, not just the isolated diff.

**Exponential backoff** — 3 retries with 2s → 4s → 8s delays on Groq API failures. Essential for production reliability.

**Async background processing** — Webhook returns 202 in < 1s. All heavy processing runs in a background task. GitHub marks deliveries as failed if no response within 10 seconds.

**Idempotency (reviews)** — Checks if the bot already reviewed a specific commit SHA before posting. Prevents duplicate reviews on webhook re-delivery.

**Idempotency (chat)** — Checks GitHub's recent comment history before replying. Works across all server processes — no in-memory state required.

**One-click fix suggestions** — When the LLM provides a concrete code fix, it is formatted as a GitHub suggestion block. Developers can apply the fix with a single click without leaving the PR.

---

## 📦 Deployment (Permanent URL)

### Railway

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Set these env vars in the Railway dashboard:
- `GROQ_API_KEY`
- `GITHUB_TOKEN`
- `WEBHOOK_SECRET`
- `SLACK_WEBHOOK_URL` (optional)

Update your GitHub webhook URL to the Railway URL. Done — always online, no ngrok needed.

### Render

1. Push to GitHub
2. New Web Service → connect repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add env vars in dashboard

---

*Powered by Groq llama-3.3-70b · Built with FastAPI · Made with ❤️ from Prashant sagar*