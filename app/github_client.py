"""
github_client.py
All GitHub API interactions:
  - Fetch PR metadata, changed files, and diffs
  - Post inline comments on specific diff lines
  - Post overall summary comment
  - Apply labels to PRs
"""

import logging
import os
from typing import Optional

from github import Github, GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

logger = logging.getLogger(__name__)

# Labels to create/apply — created automatically if they don't exist
LABEL_CONFIG = {
    "codesage: looks-good":    {"color": "0e8a16", "description": "CodeSage: No major issues found"},
    "codesage: needs-work":    {"color": "e4a923", "description": "CodeSage: Issues need to be addressed"},
    "codesage: security-risk": {"color": "d93f0b", "description": "CodeSage: Security vulnerabilities detected"},
}


class GitHubClient:
    def __init__(self):
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN environment variable is not set")
        self.gh = Github(token)

    def get_repo(self, repo_full_name: str) -> Repository:
        """Get a repository by its full name (e.g. 'owner/repo')."""
        return self.gh.get_repo(repo_full_name)

    def get_pr(self, repo_full_name: str, pr_number: int) -> PullRequest:
        """Fetch a specific Pull Request."""
        repo = self.get_repo(repo_full_name)
        return repo.get_pull(pr_number)

    def get_pr_files(self, pr: PullRequest) -> list[dict]:
        """
        Fetch all changed files in a PR.

        Returns a list of dicts:
            {filename, status, additions, deletions, patch}
        'patch' is the raw unified diff for that file.
        """
        files = []
        for f in pr.get_files():
            # Skip files with no diff (e.g. binary files, pure renames)
            if not hasattr(f, "patch") or not f.patch:
                logger.debug(f"Skipping {f.filename} — no patch available")
                continue

            files.append({
                "filename": f.filename,
                "status": f.status,          # added / modified / removed / renamed
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": f.patch,
            })

        logger.info(f"Fetched {len(files)} files with diffs for PR #{pr.number}")
        return files

    def get_pr_metadata(self, pr: PullRequest) -> dict:
        """Extract key metadata from a PR object."""
        return {
            "number": pr.number,
            "title": pr.title,
            "description": pr.body or "",
            "author": pr.user.login,
            "base_branch": pr.base.ref,
            "head_branch": pr.head.ref,
            "head_sha": pr.head.sha,
            "repo_name": pr.base.repo.full_name,
        }

    def post_review_comments(
        self,
        pr: PullRequest,
        comments: list[dict],
        summary_body: str,
    ) -> None:
        """
        Post a full code review:
          1. Inline comments on specific diff lines
          2. Overall summary comment at the bottom of the PR

        Each item in `comments` must have:
            {body, path, line}
        """
        # Post inline comments as a review
        if comments:
            try:
                pr.create_review(
                    body="",        # Review-level body (we use summary instead)
                    event="COMMENT",
                    comments=comments,
                )
                logger.info(f"Posted {len(comments)} inline comments on PR #{pr.number}")
            except GithubException as e:
                logger.error(f"Failed to post inline comments: {e}")
                # Fall back: post each as individual issue comment
                self._post_comments_as_issue_comments(pr, comments)

        # Post summary as a regular PR comment
        try:
            pr.create_issue_comment(summary_body)
            logger.info(f"Posted summary comment on PR #{pr.number}")
        except GithubException as e:
            logger.error(f"Failed to post summary comment: {e}")

    def _post_comments_as_issue_comments(self, pr: PullRequest, comments: list[dict]) -> None:
        """Fallback: post inline comments as regular issue comments."""
        for c in comments:
            try:
                pr.create_issue_comment(
                    f"**`{c['path']}` line {c['line']}**\n\n{c['body']}"
                )
            except GithubException as e:
                logger.error(f"Failed to post fallback comment: {e}")

    def apply_label(self, pr: PullRequest, label_name: str) -> None:
        """
        Apply a CodeSage label to the PR.
        Creates the label in the repo if it doesn't exist yet.
        Removes any previous CodeSage labels first.
        """
        repo = pr.base.repo

        # Remove existing CodeSage labels
        try:
            existing_labels = [l.name for l in pr.get_labels()]
            for label in existing_labels:
                if label.startswith("codesage:"):
                    pr.remove_from_labels(label)
        except GithubException as e:
            logger.warning(f"Could not remove old labels: {e}")

        # Ensure the label exists in the repo
        self._ensure_label_exists(repo, label_name)

        # Apply the new label
        try:
            pr.add_to_labels(label_name)
            logger.info(f"Applied label '{label_name}' to PR #{pr.number}")
        except GithubException as e:
            logger.error(f"Failed to apply label '{label_name}': {e}")

    def _ensure_label_exists(self, repo: Repository, label_name: str) -> None:
        """Create a label in the repo if it doesn't already exist."""
        config = LABEL_CONFIG.get(label_name, {"color": "cccccc", "description": ""})
        try:
            repo.get_label(label_name)
        except GithubException:
            try:
                repo.create_label(
                    name=label_name,
                    color=config["color"],
                    description=config["description"],
                )
                logger.info(f"Created label '{label_name}' in repo")
            except GithubException as e:
                logger.error(f"Could not create label '{label_name}': {e}")

    def has_already_reviewed(self, pr: PullRequest, bot_login: str, commit_sha: str) -> bool:
        """
        Idempotency check: returns True if the bot already reviewed this exact commit.
        Prevents duplicate reviews when GitHub re-delivers webhooks.
        """
        try:
            for comment in pr.get_issue_comments():
                if (
                    comment.user.login == bot_login
                    and "CodeSage PR Review" in comment.body
                    and commit_sha[:7] in comment.body
                ):
                    return True
        except GithubException as e:
            logger.warning(f"Could not check for existing reviews: {e}")
        return False

    def get_bot_login(self) -> Optional[str]:
        """Get the authenticated user's login name."""
        try:
            return self.gh.get_user().login
        except GithubException:
            return None
