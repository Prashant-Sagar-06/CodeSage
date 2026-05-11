"""
webhook.py
FastAPI server — receives and verifies GitHub PR webhook events.

Key design decisions:
  - Signature verification (HMAC-SHA256) on every incoming request
  - Immediate 200 response, then async background processing
    (GitHub requires a response within 10 seconds or marks delivery as failed)
  - Idempotency: skips re-reviews for already-reviewed commit SHAs
"""

import asyncio
import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.analyzer import Analyzer

logger = logging.getLogger(__name__)
router = APIRouter()

# Events we care about
HANDLED_ACTIONS = {"opened", "synchronize", "reopened"}


@router.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive GitHub webhook POST requests.

    1. Verify the HMAC-SHA256 signature
    2. Parse the event type and action
    3. Acknowledge immediately with 200
    4. Process the PR in the background
    """
    # 1. Read raw body first (must be done before JSON parsing for HMAC)
    body = await request.body()

    # 2. Verify webhook signature
    _verify_signature(body, request.headers.get("X-Hub-Signature-256", ""))

    # 3. Parse event type
    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type != "pull_request":
        logger.debug(f"Ignoring non-PR event: {event_type}")
        return Response(content="ignored", status_code=200)

    # 4. Parse payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    action = payload.get("action", "")
    if action not in HANDLED_ACTIONS:
        logger.debug(f"Ignoring PR action: {action}")
        return Response(content=f"action '{action}' not handled", status_code=200)

    # 5. Extract key identifiers
    pr_number = payload["pull_request"]["number"]
    repo_name = payload["repository"]["full_name"]
    commit_sha = payload["pull_request"]["head"]["sha"]
    pr_title = payload["pull_request"]["title"]

    logger.info(f"Received PR event: {repo_name}#{pr_number} action={action} sha={commit_sha[:7]}")

    # 6. Return 200 immediately, then process in background
    background_tasks.add_task(
        _process_pr_review,
        repo_name=repo_name,
        pr_number=pr_number,
        commit_sha=commit_sha,
        pr_title=pr_title,
    )

    return Response(
        content=f"Review queued for PR #{pr_number}",
        status_code=202,
    )


@router.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "service": "codesage-pr-bot"}


# ─── Background task ──────────────────────────────────────────────────────────

async def _process_pr_review(
    repo_name: str,
    pr_number: int,
    commit_sha: str,
    pr_title: str,
) -> None:
    """Run the full PR analysis pipeline in the background."""
    logger.info(f"[Background] Starting review for {repo_name}#{pr_number}")

    try:
        analyzer = Analyzer()

        # Idempotency: skip if already reviewed this commit
        pr = analyzer.github.get_pr(repo_name, pr_number)
        bot_login = analyzer.github.get_bot_login()
        if bot_login and analyzer.github.has_already_reviewed(pr, bot_login, commit_sha):
            logger.info(
                f"[Background] Skipping — already reviewed commit {commit_sha[:7]} "
                f"for PR #{pr_number}"
            )
            return

        # Run analysis
        review = await analyzer.analyze_pr(repo_name, pr_number)
        if not review:
            logger.warning(f"[Background] Analysis returned no review for PR #{pr_number}")
            return

        # Post results to GitHub
        success = await analyzer.post_review(repo_name, pr_number, review)
        if success:
            logger.info(
                f"[Background] ✅ Review posted for {repo_name}#{pr_number} "
                f"(score={review.score}, label={review.label})"
            )
        else:
            logger.error(f"[Background] ❌ Failed to post review for PR #{pr_number}")

    except Exception as e:
        logger.exception(
            f"[Background] Unhandled error processing PR #{pr_number}: {e}"
        )


# ─── Signature verification ───────────────────────────────────────────────────

def _verify_signature(body: bytes, signature_header: str) -> None:
    """
    Verify the GitHub webhook HMAC-SHA256 signature.
    Raises HTTPException 401 if verification fails.

    GitHub sets X-Hub-Signature-256: sha256=<hex_digest>
    """
    secret = os.getenv("WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("WEBHOOK_SECRET not set — skipping signature verification (UNSAFE)")
        return

    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid signature format")

    expected_sig = signature_header[len("sha256="):]
    computed_sig = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_sig, expected_sig):
        logger.warning("Webhook signature verification FAILED — possible spoofed request")
        raise HTTPException(status_code=401, detail="Webhook signature verification failed")

    logger.debug("Webhook signature verified ✓")
