"""
slack_notifier.py
Feature #13 — Slack notifications for critical issues and security risks.

Uses Slack Incoming Webhooks — no OAuth needed, just a webhook URL.
Setup: https://api.slack.com/messaging/webhooks

Set SLACK_WEBHOOK_URL in your .env to enable.
Respects per-repo slack_notify and slack_channel settings from .codesage.yml.
"""

import logging
import os
from typing import Optional

import httpx

from models.review_model import PRReview

logger = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self):
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self.enabled = bool(self.webhook_url)
        if not self.enabled:
            logger.debug("SLACK_WEBHOOK_URL not set — Slack notifications disabled")

    async def notify_pr_reviewed(
        self,
        review: PRReview,
        pr_url: str,
        pr_title: str,
        pr_author: str,
        channel: Optional[str] = None,
    ) -> bool:
        """
        Send a Slack notification when a PR review is complete.

        Always notifies for:
          - Security risks (any severity with security keyword)
          - Critical bugs found

        Sends a lighter summary for clean PRs (score ≥ 85).
        """
        if not self.enabled:
            return False

        # Build the message payload
        if review.label == "codesage: security-risk":
            payload = self._security_risk_message(review, pr_url, pr_title, pr_author, channel)
        elif review.critical_comments:
            payload = self._critical_bugs_message(review, pr_url, pr_title, pr_author, channel)
        elif review.score >= 85:
            payload = self._clean_pr_message(review, pr_url, pr_title, pr_author, channel)
        else:
            payload = self._needs_work_message(review, pr_url, pr_title, pr_author, channel)

        return await self._send(payload)

    async def notify_chat_reply(
        self,
        repo_name: str,
        pr_number: int,
        pr_url: str,
        question: str,
        channel: Optional[str] = None,
    ) -> bool:
        """Notify Slack when someone asks CodeSage a question on a PR."""
        if not self.enabled:
            return False

        payload = {
            "channel": channel or "#general",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"💬 *Someone asked CodeSage a question on "
                            f"<{pr_url}|{repo_name}#{pr_number}>*\n"
                            f"> {question[:200]}"
                        ),
                    },
                }
            ],
        }
        return await self._send(payload)

    # ─── Message builders ─────────────────────────────────────────────────────

    def _security_risk_message(
        self, review: PRReview, pr_url: str, pr_title: str, pr_author: str, channel
    ) -> dict:
        critical_list = "\n".join(
            f"• `{c.file_path}` L{c.line} — {c.title}"
            for c in review.critical_comments[:5]
        )
        return {
            "channel": channel or "#general",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🚨 CodeSage: Security Risk Detected",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*PR:*\n<{pr_url}|{pr_title}>"},
                        {"type": "mrkdwn", "text": f"*Author:*\n@{pr_author}"},
                        {"type": "mrkdwn", "text": f"*Score:*\n{review.score}/100"},
                        {"type": "mrkdwn", "text": f"*Repo:*\n{review.repo_name}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Security Issues Found:*\n{critical_list}",
                    },
                },
                {"type": "divider"},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View PR"},
                            "url": pr_url,
                            "style": "danger",
                        }
                    ],
                },
            ],
        }

    def _critical_bugs_message(
        self, review: PRReview, pr_url: str, pr_title: str, pr_author: str, channel
    ) -> dict:
        bug_list = "\n".join(
            f"• `{c.file_path}` L{c.line} — {c.title}"
            for c in review.critical_comments[:5]
        )
        return {
            "channel": channel or "#general",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🔴 CodeSage: {len(review.critical_comments)} Critical Bug(s) Found",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*PR:*\n<{pr_url}|{pr_title}>"},
                        {"type": "mrkdwn", "text": f"*Author:*\n@{pr_author}"},
                        {"type": "mrkdwn", "text": f"*Score:*\n{review.score}/100"},
                        {"type": "mrkdwn", "text": f"*Files Reviewed:*\n{review.files_reviewed}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Critical Issues:*\n{bug_list}"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View PR"},
                            "url": pr_url,
                            "style": "danger",
                        }
                    ],
                },
            ],
        }

    def _clean_pr_message(
        self, review: PRReview, pr_url: str, pr_title: str, pr_author: str, channel
    ) -> dict:
        return {
            "channel": channel or "#general",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"✅ *CodeSage: <{pr_url}|{pr_title}> looks good!*\n"
                            f"Score: *{review.score}/100* · Author: @{pr_author} · "
                            f"Files: {review.files_reviewed}"
                        ),
                    },
                }
            ],
        }

    def _needs_work_message(
        self, review: PRReview, pr_url: str, pr_title: str, pr_author: str, channel
    ) -> dict:
        return {
            "channel": channel or "#general",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"⚠️ *CodeSage: <{pr_url}|{pr_title}> needs work*\n"
                            f"Score: *{review.score}/100* · "
                            f"{len(review.warning_comments)} warning(s) · "
                            f"Author: @{pr_author}"
                        ),
                    },
                }
            ],
        }

    # ─── HTTP sender ──────────────────────────────────────────────────────────

    async def _send(self, payload: dict) -> bool:
        """POST the payload to the Slack webhook URL."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                if resp.status_code == 200 and resp.text == "ok":
                    logger.info("Slack notification sent successfully")
                    return True
                else:
                    logger.warning(
                        f"Slack returned unexpected response: "
                        f"{resp.status_code} — {resp.text}"
                    )
                    return False
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False