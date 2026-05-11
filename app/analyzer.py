"""
analyzer.py
The main orchestrator for CodeSage.

Flow:
  For each PR file:
    1. Build prompt  (prompt_builder)
    2. Call Groq     (llm_engine)
    3. Parse result  (parser)
  Then aggregate all file reviews into a single PRReview and
  hand it back to the webhook handler for posting to GitHub.
"""

import asyncio
import logging
from statistics import mean
from typing import Optional

from app.github_client import GitHubClient
from app.llm_engine import LLMEngine
from app.parser import parse_llm_response
from app.prompt_builder import build_file_prompt, detect_language
from models.review_model import PRReview, ReviewComment

logger = logging.getLogger(__name__)

# Caps to keep latency and cost reasonable
MAX_FILES_PER_PR = 15          # Skip files beyond this count
MAX_DIFF_LINES = 500           # Truncate huge diffs


class Analyzer:
    def __init__(self):
        self.github = GitHubClient()
        self.llm = LLMEngine()

    async def analyze_pr(self, repo_name: str, pr_number: int) -> Optional[PRReview]:
        """
        Full pipeline: fetch PR → review each file → aggregate → return PRReview.
        Returns None if the PR cannot be fetched or has no reviewable files.
        """
        logger.info(f"Starting analysis of {repo_name}#{pr_number}")

        # 1. Fetch PR and its files from GitHub
        try:
            pr = self.github.get_pr(repo_name, pr_number)
            metadata = self.github.get_pr_metadata(pr)
            files = self.github.get_pr_files(pr)
        except Exception as e:
            logger.error(f"Failed to fetch PR data: {e}")
            return None

        if not files:
            logger.warning(f"No reviewable files in PR #{pr_number}")
            return None

        # 2. Cap file count and log a warning if we're truncating
        if len(files) > MAX_FILES_PER_PR:
            logger.warning(
                f"PR has {len(files)} files — reviewing first {MAX_FILES_PER_PR} only"
            )
            files = files[:MAX_FILES_PER_PR]

        # 3. Review each file concurrently (but with a semaphore to avoid hammering Groq)
        semaphore = asyncio.Semaphore(3)   # Max 3 concurrent Groq calls
        tasks = [
            self._review_file(f, metadata, semaphore)
            for f in files
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4. Aggregate results
        all_comments: list[ReviewComment] = []
        scores: list[int] = []
        detected_language = "Unknown"

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"File review task failed: {result}")
                continue
            if result is None:
                continue
            file_comments, file_score, file_language = result
            all_comments.extend(file_comments)
            scores.append(file_score)
            if file_language != "Unknown":
                detected_language = file_language

        # Final score: average of all file scores, weighted slightly toward worst
        final_score = _compute_final_score(scores, all_comments)

        # 5. Build and return the PRReview
        review = PRReview(
            pr_number=pr_number,
            repo_name=repo_name,
            score=final_score,
            language=detected_language,
            files_reviewed=len(files),
            comments=all_comments,
            summary=_build_summary_narrative(all_comments, final_score),
        )

        logger.info(
            f"Analysis complete for PR #{pr_number}: "
            f"score={final_score}, comments={len(all_comments)}, "
            f"critical={len(review.critical_comments)}"
        )
        return review

    async def _review_file(
        self,
        file_data: dict,
        metadata: dict,
        semaphore: asyncio.Semaphore,
    ) -> Optional[tuple]:
        """Review a single file and return (comments, score, language)."""
        filename = file_data["filename"]
        patch = file_data["patch"]

        # Truncate very large diffs
        diff_lines = patch.split("\n")
        if len(diff_lines) > MAX_DIFF_LINES:
            logger.warning(f"Truncating diff for {filename} ({len(diff_lines)} → {MAX_DIFF_LINES} lines)")
            patch = "\n".join(diff_lines[:MAX_DIFF_LINES]) + "\n... (truncated)"

        language = detect_language(filename)
        messages = build_file_prompt(
            filename=filename,
            file_diff=patch,
            pr_title=metadata["title"],
            pr_description=metadata["description"],
            detected_language=language,
        )

        async with semaphore:
            raw_response = await self.llm.review_file(messages)

        if not raw_response:
            logger.warning(f"No LLM response for {filename}")
            return None

        comments, score, detected_language = parse_llm_response(raw_response, filename)
        return comments, score, detected_language

    async def post_review(self, repo_name: str, pr_number: int, review: PRReview) -> bool:
        """Post the completed review to GitHub (comments + label)."""
        try:
            pr = self.github.get_pr(repo_name, pr_number)

            # Build inline comment payloads (GitHub API format)
            inline_comments = []
            for c in review.comments:
                inline_comments.append({
                    "path": c.file_path,
                    "line": c.line,
                    "body": c.to_github_body(),
                })

            # Add commit SHA to summary for idempotency tracking
            head_sha = pr.head.sha
            summary = review.to_summary_comment()
            summary += f"\n\n<!-- codesage-sha:{head_sha[:7]} -->"

            self.github.post_review_comments(pr, inline_comments, summary)
            self.github.apply_label(pr, review.label)
            return True

        except Exception as e:
            logger.error(f"Failed to post review for PR #{pr_number}: {e}")
            return False


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _compute_final_score(scores: list[int], comments: list[ReviewComment]) -> int:
    """
    Compute an overall score from per-file scores.
    Penalties:
      - Each critical issue: -5 points
      - Each warning: -2 points
    """
    if not scores:
        return 50

    base = int(mean(scores))

    critical_count = sum(1 for c in comments if c.severity == "critical")
    warning_count = sum(1 for c in comments if c.severity == "warning")

    penalized = base - (critical_count * 5) - (warning_count * 2)
    return max(0, min(100, penalized))


def _build_summary_narrative(comments: list[ReviewComment], score: int) -> str:
    """Build a brief overall narrative for the PR summary."""
    critical = [c for c in comments if c.severity == "critical"]
    warnings = [c for c in comments if c.severity == "warning"]

    if not comments:
        return "No issues found. This looks clean! ✅"

    if score >= 85:
        return (
            f"Overall this is a solid PR. "
            f"Found {len(warnings)} minor warning(s) worth addressing but nothing blocking."
        )
    elif critical:
        issues = ", ".join(f"`{c.file_path}` ({c.title})" for c in critical[:3])
        return (
            f"There are {len(critical)} critical issue(s) that must be fixed before merging: "
            f"{issues}. Please address these before requesting re-review."
        )
    else:
        return (
            f"Found {len(warnings)} warning(s) that should be addressed. "
            f"No critical blockers, but quality could be improved."
        )
