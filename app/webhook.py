"""
webhook.py
Updated FastAPI webhook server — now handles:
  - Feature #1: PR comment events for @codesage chat
  - PR opened/updated events (original behaviour)
"""

import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.analyzer import Analyzer
from app.chat_handler import ChatHandler
from app.config_loader import load_config

logger = logging.getLogger(__name__)
router = APIRouter()

HANDLED_PR_ACTIONS = {"opened", "synchronize", "reopened"}

# Idempotency: track comment IDs we already processed this session
# Prevents duplicate replies when GitHub re-delivers webhooks
_processed_comment_ids: set[int] = set()


# ─── Main webhook endpoint ────────────────────────────────────────────────────

@router.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives all GitHub webhook events.
    Routes to PR review pipeline or @codesage chat handler.
    """
    body = await request.body()
    _verify_signature(body, request.headers.get("X-Hub-Signature-256", ""))

    event_type = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()

    # ── Pull Request events (open / update) ───────────────────────────────────
    if event_type == "pull_request":
        action = payload.get("action", "")
        if action not in HANDLED_PR_ACTIONS:
            return Response(content=f"action '{action}' not handled", status_code=200)

        pr_number  = payload["pull_request"]["number"]
        repo_name  = payload["repository"]["full_name"]
        commit_sha = payload["pull_request"]["head"]["sha"]
        pr_title   = payload["pull_request"]["title"]
        pr_author  = payload["pull_request"]["user"]["login"]
        pr_url     = payload["pull_request"]["html_url"]

        logger.info(f"PR event: {repo_name}#{pr_number} action={action}")

        background_tasks.add_task(
            _process_pr_review,
            repo_name=repo_name,
            pr_number=pr_number,
            commit_sha=commit_sha,
            pr_title=pr_title,
            pr_author=pr_author,
            pr_url=pr_url,
        )
        return Response(content=f"Review queued for PR #{pr_number}", status_code=202)

    # ── Issue comment events (@codesage chat — Feature #1) ────────────────────
# ── Issue comment events (@codesage chat — Feature #1) ────────────────────
    if event_type == "issue_comment":
        action = payload.get("action", "")
        if action != "created":
            return Response(content="ignored", status_code=200)

        # Only on Pull Requests, not plain issues
        if "pull_request" not in payload.get("issue", {}):
            return Response(content="not a PR comment", status_code=200)

        comment = payload.get("comment", {})
        comment_body   = comment.get("body", "")
        comment_id     = comment.get("id", 0)
        comment_author = comment.get("user", {}).get("login", "")

        # FIX 1 — Never reply to our own comments (prevents infinite loop)
        bot_login = payload["repository"]["owner"]["login"]
        if comment_author.lower() == bot_login.lower():
            logger.debug(f"Skipping own comment from {comment_author}")
            return Response(content="own comment", status_code=200)

        # Also skip if commenter login contains "[bot]"
        if "[bot]" in comment_author.lower() or comment_author.lower().endswith("-bot"):
            return Response(content="bot comment ignored", status_code=200)

        # FIX 2 — Skip if we already processed this exact comment ID
        if comment_id in _processed_comment_ids:
            logger.info(f"Duplicate delivery for comment {comment_id}, skipping")
            return Response(content="already processed", status_code=200)

        from app.chat_handler import ChatHandler
        handler = ChatHandler()
        if not handler.is_codesage_mention(comment_body):
            return Response(content="no mention", status_code=200)

        # Mark as processed BEFORE queuing to block any re-delivery
        _processed_comment_ids.add(comment_id)
        # Keep the set small — only remember last 500 comments
        if len(_processed_comment_ids) > 500:
            _processed_comment_ids.clear()

        pr_number = payload["issue"]["number"]
        repo_name = payload["repository"]["full_name"]
        pr_url    = payload["issue"].get("html_url", "")

        logger.info(f"@codesage mention in {repo_name}#{pr_number} comment_id={comment_id}")

        background_tasks.add_task(
            _process_chat_reply,
            repo_name=repo_name,
            pr_number=pr_number,
            comment_body=comment_body,
            pr_url=pr_url,
        )
        return Response(content="Chat reply queued", status_code=202)

    logger.debug(f"Ignoring event: {event_type}")
    return Response(content="ignored", status_code=200)


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "codesage-pr-bot"}


# ─── Background tasks ─────────────────────────────────────────────────────────

async def _process_pr_review(
    repo_name: str,
    pr_number: int,
    commit_sha: str,
    pr_title: str,
    pr_author: str,
    pr_url: str,
) -> None:
    logger.info(f"[Background] Reviewing {repo_name}#{pr_number}")
    try:
        analyzer = Analyzer()

        # Idempotency check
        pr = analyzer.github.get_pr(repo_name, pr_number)
        bot_login = analyzer.github.get_bot_login()
        if bot_login and analyzer.github.has_already_reviewed(pr, bot_login, commit_sha):
            logger.info(f"[Background] Already reviewed {commit_sha[:7]}, skipping")
            return

        # Load config for Slack channel / settings
        repo = analyzer.github.get_repo(repo_name)
        config = load_config(repo, ref=commit_sha)

        review = await analyzer.analyze_pr(repo_name, pr_number)
        if not review:
            logger.warning(f"[Background] No review produced for PR #{pr_number}")
            return

        success = await analyzer.post_review(
            repo_name=repo_name,
            pr_number=pr_number,
            review=review,
            config=config,
            pr_url=pr_url,
            pr_title=pr_title,
            pr_author=pr_author,
        )

        status = "✅ posted" if success else "❌ failed to post"
        logger.info(f"[Background] {status} review for PR #{pr_number} (score={review.score if review else 'N/A'})")

    except Exception as e:
        logger.exception(f"[Background] Error processing PR #{pr_number}: {e}")


async def _process_chat_reply(
    repo_name: str,
    pr_number: int,
    comment_body: str,
    pr_url: str,
) -> None:
    """Feature #1 — generate and post a @codesage chat reply."""
    logger.info(f"[Background] Generating chat reply for {repo_name}#{pr_number}")
    try:
        analyzer = Analyzer()
        handler = ChatHandler()

        pr = analyzer.github.get_pr(repo_name, pr_number)
        repo = analyzer.github.get_repo(repo_name)
        config = load_config(repo, ref=pr.head.sha)

        reply = await handler.handle_comment(
            pr=pr,
            comment=type("Comment", (), {"body": comment_body})(),
            language=config.language,
        )

        if reply:
            pr.create_issue_comment(reply)
            logger.info(f"[Background] Chat reply posted on PR #{pr_number}")

            # Notify Slack about the chat interaction
            if config.slack_notify:
                question = handler.extract_question(comment_body)
                await analyzer.slack.notify_chat_reply(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    pr_url=pr_url,
                    question=question,
                    channel=config.slack_channel,
                )
        else:
            logger.warning(f"[Background] No chat reply generated for PR #{pr_number}")

    except Exception as e:
        logger.exception(f"[Background] Chat reply error for PR #{pr_number}: {e}")


# ─── Signature verification ───────────────────────────────────────────────────

def _verify_signature(body: bytes, signature_header: str) -> None:
    secret = os.getenv("WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("WEBHOOK_SECRET not set — skipping verification (UNSAFE)")
        return
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256")
    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid signature format")

    expected = signature_header[len("sha256="):]
    computed = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, expected):
        raise HTTPException(status_code=401, detail="Signature verification failed")