"""
parser.py
Parses the raw JSON string from Groq into typed ReviewComment objects.
Handles malformed responses, missing fields, and edge cases gracefully.
"""

import json
import logging
from typing import Optional

from models.review_model import ReviewComment

logger = logging.getLogger(__name__)

VALID_SEVERITIES = {"critical", "warning", "info"}


def parse_llm_response(
    raw_json: str,
    file_path: str,
) -> tuple[list[ReviewComment], int, str]:
    """
    Parse the LLM's JSON response into ReviewComment objects.

    Args:
        raw_json:  Raw string from Groq API
        file_path: The file being reviewed (for populating ReviewComment.file_path)

    Returns:
        (comments, score, language)
        - comments: list of ReviewComment objects (may be empty on parse failure)
        - score: int 0-100 (defaults to 50 on failure)
        - language: detected language string
    """
    if not raw_json or not raw_json.strip():
        logger.warning(f"Empty response from LLM for {file_path}")
        return [], 50, "Unknown"

    # Strip accidental markdown fences (model sometimes ignores instructions)
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed for {file_path}: {e}\nRaw: {raw_json[:200]}")
        return [], 50, "Unknown"

    comments = _parse_comments(data.get("comments", []), file_path)
    score = _parse_score(data.get("score", 50))
    language = str(data.get("language", "Unknown")).strip() or "Unknown"

    logger.info(
        f"Parsed {len(comments)} comments for {file_path} "
        f"(score={score}, language={language})"
    )
    return comments, score, language


def _parse_comments(raw_comments: list, file_path: str) -> list[ReviewComment]:
    """Parse the comments array from the LLM response."""
    if not isinstance(raw_comments, list):
        return []

    comments = []
    for i, item in enumerate(raw_comments):
        if not isinstance(item, dict):
            logger.debug(f"Skipping non-dict comment at index {i}")
            continue

        comment = _parse_single_comment(item, file_path)
        if comment:
            comments.append(comment)

    return comments


def _parse_single_comment(item: dict, file_path: str) -> Optional[ReviewComment]:
    """Parse and validate a single comment dict."""
    # Validate and coerce line number
    try:
        line = int(item.get("line", 1))
        if line < 1:
            line = 1
    except (ValueError, TypeError):
        line = 1

    # Validate severity
    severity = str(item.get("severity", "info")).lower().strip()
    if severity not in VALID_SEVERITIES:
        logger.debug(f"Invalid severity '{severity}', defaulting to 'info'")
        severity = "info"

    # Require at minimum a title
    title = str(item.get("title", "")).strip()
    if not title:
        logger.debug("Skipping comment with no title")
        return None

    description = str(item.get("description", "")).strip()
    suggestion = str(item.get("suggestion", "")).strip()

    return ReviewComment(
        file_path=file_path,
        line=line,
        severity=severity,
        title=title,
        description=description,
        suggestion=suggestion,
    )


def _parse_score(raw_score) -> int:
    """Parse and clamp score to 0-100."""
    try:
        score = int(raw_score)
        return max(0, min(100, score))
    except (ValueError, TypeError):
        return 50
