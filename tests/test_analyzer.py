"""
tests/test_analyzer.py
Unit tests for the Analyzer orchestrator and PRReview model logic.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.review_model import ReviewComment, PRReview
from app.analyzer import _compute_final_score, _build_summary_narrative


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_comment(severity="warning", file_path="main.py", line=10, title="Test issue"):
    return ReviewComment(
        file_path=file_path,
        line=line,
        severity=severity,
        title=title,
        description="A test issue description.",
        suggestion="Fix it like this.",
    )


def make_review(score=75, comments=None):
    return PRReview(
        pr_number=42,
        repo_name="owner/repo",
        score=score,
        language="Python",
        files_reviewed=3,
        comments=comments or [],
    )


# ─── ReviewComment tests ──────────────────────────────────────────────────────

class TestReviewComment:
    def test_to_github_body_critical(self):
        c = make_comment(severity="critical", title="Null pointer dereference")
        body = c.to_github_body()
        assert "🔴" in body
        assert "CRITICAL" in body
        assert "Null pointer dereference" in body

    def test_to_github_body_warning(self):
        c = make_comment(severity="warning")
        body = c.to_github_body()
        assert "🟡" in body
        assert "WARNING" in body

    def test_to_github_body_info(self):
        c = make_comment(severity="info")
        body = c.to_github_body()
        assert "🔵" in body

    def test_suggestion_included(self):
        c = make_comment()
        body = c.to_github_body()
        assert "Fix it like this." in body


# ─── PRReview tests ───────────────────────────────────────────────────────────

class TestPRReview:
    def test_label_looks_good(self):
        review = make_review(score=90)
        assert review.label == "codesage: looks-good"

    def test_label_needs_work(self):
        review = make_review(score=60)
        assert review.label == "codesage: needs-work"

    def test_label_security_risk(self):
        review = make_review(
            score=50,
            comments=[make_comment(severity="critical", title="SQL injection found")]
        )
        assert review.label == "codesage: security-risk"

    def test_critical_comment_filter(self):
        comments = [
            make_comment("critical"),
            make_comment("warning"),
            make_comment("info"),
            make_comment("critical"),
        ]
        review = make_review(comments=comments)
        assert len(review.critical_comments) == 2
        assert len(review.warning_comments) == 1
        assert len(review.info_comments) == 1

    def test_summary_comment_contains_score(self):
        review = make_review(score=72, comments=[make_comment("critical", title="Crash bug")])
        summary = review.to_summary_comment()
        assert "72/100" in summary
        assert "Critical Issues" in summary
        assert "Crash bug" in summary

    def test_summary_no_issues(self):
        review = make_review(score=95, comments=[])
        summary = review.to_summary_comment()
        assert "95/100" in summary


# ─── Analyzer helper tests ────────────────────────────────────────────────────

class TestComputeFinalScore:
    def test_no_scores_returns_50(self):
        assert _compute_final_score([], []) == 50

    def test_single_score_no_comments(self):
        assert _compute_final_score([80], []) == 80

    def test_critical_penalty(self):
        comments = [make_comment("critical"), make_comment("critical")]
        score = _compute_final_score([80], comments)
        assert score == 70  # 80 - (2 * 5)

    def test_warning_penalty(self):
        comments = [make_comment("warning"), make_comment("warning")]
        score = _compute_final_score([80], comments)
        assert score == 76  # 80 - (2 * 2)

    def test_score_never_below_0(self):
        comments = [make_comment("critical")] * 30
        score = _compute_final_score([50], comments)
        assert score == 0

    def test_score_never_above_100(self):
        assert _compute_final_score([100], []) == 100

    def test_averages_multiple_file_scores(self):
        score = _compute_final_score([80, 60], [])
        assert score == 70


class TestBuildSummaryNarrative:
    def test_no_issues(self):
        narrative = _build_summary_narrative([], 95)
        assert "clean" in narrative.lower() or "no issues" in narrative.lower()

    def test_good_score(self):
        comments = [make_comment("warning")]
        narrative = _build_summary_narrative(comments, 88)
        assert "solid" in narrative.lower()

    def test_critical_issues(self):
        comments = [make_comment("critical", title="Use after free")]
        narrative = _build_summary_narrative(comments, 40)
        assert "critical" in narrative.lower()
        assert "must be fixed" in narrative.lower()
