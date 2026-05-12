"""
analyzer.py
Updated orchestrator — now integrates:
  - Feature #3:  Auto-fix suggestion blocks
  - Feature #9:  Context-aware reviews (related file fetching)
  - Feature #13: Slack notifications
  - Feature #16: .codesage.yml config per repo
  - Feature #18: Multilingual output
"""

import asyncio
import logging
from statistics import mean
from typing import Optional

from app.autofix import build_suggestion_comment, has_actionable_fix
from app.config_loader import CodeSageConfig, load_config, should_ignore_file
from app.context_fetcher import fetch_file_context, format_context_for_prompt
from app.github_client import GitHubClient
from app.llm_engine import LLMEngine
from app.parser import parse_llm_response
from app.prompt_builder import build_file_prompt, detect_language, get_system_prompt
from app.slack_notifier import SlackNotifier
from models.review_model import PRReview, ReviewComment

logger = logging.getLogger(__name__)

MAX_DIFF_LINES = 500


class Analyzer:
    def __init__(self):
        self.github = GitHubClient()
        self.llm = LLMEngine()
        self.slack = SlackNotifier()

    async def analyze_pr(self, repo_name: str, pr_number: int) -> Optional[PRReview]:
        """
        Full pipeline: load config → fetch PR → review each file → aggregate → return PRReview.
        """
        logger.info(f"Starting analysis of {repo_name}#{pr_number}")

        try:
            pr = self.github.get_pr(repo_name, pr_number)
            metadata = self.github.get_pr_metadata(pr)
            repo = self.github.get_repo(repo_name)
        except Exception as e:
            logger.error(f"Failed to fetch PR data: {e}")
            return None

        # Load per-repo config from .codesage.yml
        config = load_config(repo, ref=metadata["head_sha"])
        logger.info(f"Config loaded: context_aware={config.context_aware}, lang={config.language}, max_files={config.max_files}")

        try:
            files = self.github.get_pr_files(pr)
        except Exception as e:
            logger.error(f"Failed to fetch PR files: {e}")
            return None

        if not files:
            logger.warning(f"No reviewable files in PR #{pr_number}")
            return None

        # Filter ignored files from config
        original_count = len(files)
        files = [
            f for f in files
            if not should_ignore_file(f["filename"], config.ignore_files)
        ]
        if len(files) < original_count:
            logger.info(f"Ignored {original_count - len(files)} files per .codesage.yml")

        # Cap file count
        if len(files) > config.max_files:
            logger.warning(f"Capping at {config.max_files} files (PR has {len(files)})")
            files = files[:config.max_files]

        # Review each file concurrently (max 3 at a time)
        semaphore = asyncio.Semaphore(3)
        tasks = [
            self._review_file(f, metadata, config, repo, pr, semaphore)
            for f in files
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate
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

        final_score = _compute_final_score(scores, all_comments)

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
        config: CodeSageConfig,
        repo,
        pr,
        semaphore: asyncio.Semaphore,
    ) -> Optional[tuple]:
        """Review a single file — returns (comments, score, language)."""
        filename = file_data["filename"]
        patch = file_data["patch"]

        # Truncate large diffs
        diff_lines = patch.split("\n")
        if len(diff_lines) > MAX_DIFF_LINES:
            logger.warning(f"Truncating diff for {filename}")
            patch = "\n".join(diff_lines[:MAX_DIFF_LINES]) + "\n... (truncated)"

        language = detect_language(filename)

        # Feature #9 — fetch related context if enabled
        related_context = ""
        if config.context_aware:
            try:
                related_files = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_file_context, repo, filename, pr
                )
                related_context = format_context_for_prompt(related_files)
            except Exception as e:
                logger.warning(f"Context fetch failed for {filename}: {e}")

        messages = build_file_prompt(
            filename=filename,
            file_diff=patch,
            pr_title=metadata["title"],
            pr_description=metadata["description"],
            detected_language=language,
            related_context=related_context,
            custom_instructions=config.custom_instructions,
        )

        # Feature #18 — multilingual system prompt
        system_prompt = get_system_prompt(config.language)

        async with semaphore:
            raw_response = await self.llm.review_file(messages, system_prompt_override=system_prompt)

        if not raw_response:
            logger.warning(f"No LLM response for {filename}")
            return None

        comments, score, detected_language = parse_llm_response(raw_response, filename)

        # Filter by severity_filter from config
        comments = [c for c in comments if c.severity in config.severity_filter]

        return comments, score, detected_language

    async def post_review(
        self,
        repo_name: str,
        pr_number: int,
        review: PRReview,
        config: Optional[CodeSageConfig] = None,
        pr_url: str = "",
        pr_title: str = "",
        pr_author: str = "",
    ) -> bool:
        """Post the completed review to GitHub + send Slack notification."""
        try:
            pr = self.github.get_pr(repo_name, pr_number)

            # Build inline comment payloads
            # Feature #3 — use suggestion blocks where possible
            inline_comments = []
            for c in review.comments:
                if has_actionable_fix(c.suggestion):
                    body = build_suggestion_comment(
                        original_line="",
                        suggestion_text=c.suggestion,
                        severity=c.severity,
                        title=c.title,
                        description=c.description,
                    )
                else:
                    body = c.to_github_body()

                inline_comments.append({
                    "path": c.file_path,
                    "line": c.line,
                    "body": body,
                })

            # Add commit SHA for idempotency
            head_sha = pr.head.sha
            summary = review.to_summary_comment()
            summary += f"\n\n<!-- codesage-sha:{head_sha[:7]} -->"

            self.github.post_review_comments(pr, inline_comments, summary)
            self.github.apply_label(pr, review.label)

            # Feature #13 — Slack notification
            slack_channel = config.slack_channel if config else "#general"
            slack_enabled = config.slack_notify if config else True

            if slack_enabled and pr_url:
                await self.slack.notify_pr_reviewed(
                    review=review,
                    pr_url=pr_url,
                    pr_title=pr_title or f"PR #{pr_number}",
                    pr_author=pr_author or "unknown",
                    channel=slack_channel,
                )

            return True

        except Exception as e:
            logger.error(f"Failed to post review for PR #{pr_number}: {e}")
            return False


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _compute_final_score(scores: list[int], comments: list[ReviewComment]) -> int:
    if not scores:
        return 50
    base = int(mean(scores))
    critical_count = sum(1 for c in comments if c.severity == "critical")
    warning_count = sum(1 for c in comments if c.severity == "warning")
    penalized = base - (critical_count * 5) - (warning_count * 2)
    return max(0, min(100, penalized))


def _build_summary_narrative(comments: list[ReviewComment], score: int) -> str:
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