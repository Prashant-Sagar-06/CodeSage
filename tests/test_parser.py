"""
tests/test_parser.py
Unit tests for the LLM response parser.
"""

import json
import pytest

from app.parser import parse_llm_response, _parse_score


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def make_response(comments=None, score=85, language="Python", optimizations=None):
    return json.dumps({
        "comments": comments or [],
        "optimizations": optimizations or [],
        "complexity": "medium",
        "score": score,
        "language": language,
    })


VALID_COMMENT = {
    "line": 14,
    "severity": "critical",
    "title": "Division by zero",
    "description": "Variable `b` can be 0 here, causing a ZeroDivisionError at runtime.",
    "suggestion": "Add a guard: `if b == 0: return None`",
}


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestParseScore:
    def test_valid_score(self):
        assert _parse_score(75) == 75

    def test_clamp_high(self):
        assert _parse_score(150) == 100

    def test_clamp_low(self):
        assert _parse_score(-10) == 0

    def test_string_number(self):
        assert _parse_score("72") == 72

    def test_invalid_falls_back_to_50(self):
        assert _parse_score("not-a-number") == 50

    def test_none_falls_back_to_50(self):
        assert _parse_score(None) == 50


class TestParseLLMResponse:
    def test_valid_response_with_comment(self):
        raw = make_response(comments=[VALID_COMMENT], score=72)
        comments, score, language = parse_llm_response(raw, "utils.py")

        assert len(comments) == 1
        assert comments[0].file_path == "utils.py"
        assert comments[0].line == 14
        assert comments[0].severity == "critical"
        assert comments[0].title == "Division by zero"
        assert score == 72
        assert language == "Python"

    def test_empty_comments(self):
        raw = make_response(comments=[], score=95)
        comments, score, language = parse_llm_response(raw, "clean.py")
        assert comments == []
        assert score == 95

    def test_empty_string_returns_defaults(self):
        comments, score, language = parse_llm_response("", "file.py")
        assert comments == []
        assert score == 50
        assert language == "Unknown"

    def test_invalid_json_returns_defaults(self):
        comments, score, language = parse_llm_response("not json at all {{{", "file.py")
        assert comments == []
        assert score == 50

    def test_markdown_fenced_json_is_cleaned(self):
        raw = "```json\n" + make_response(comments=[VALID_COMMENT]) + "\n```"
        comments, score, language = parse_llm_response(raw, "utils.py")
        assert len(comments) == 1

    def test_invalid_severity_defaults_to_info(self):
        bad_comment = {**VALID_COMMENT, "severity": "blocker"}
        raw = make_response(comments=[bad_comment])
        comments, _, _ = parse_llm_response(raw, "file.py")
        assert comments[0].severity == "info"

    def test_invalid_line_defaults_to_1(self):
        bad_comment = {**VALID_COMMENT, "line": "not-a-line"}
        raw = make_response(comments=[bad_comment])
        comments, _, _ = parse_llm_response(raw, "file.py")
        assert comments[0].line == 1

    def test_comment_without_title_is_skipped(self):
        no_title = {**VALID_COMMENT, "title": ""}
        raw = make_response(comments=[no_title])
        comments, _, _ = parse_llm_response(raw, "file.py")
        assert comments == []

    def test_multiple_comments(self):
        second = {**VALID_COMMENT, "line": 25, "severity": "warning", "title": "Unused import"}
        raw = make_response(comments=[VALID_COMMENT, second])
        comments, _, _ = parse_llm_response(raw, "main.py")
        assert len(comments) == 2
        severities = {c.severity for c in comments}
        assert severities == {"critical", "warning"}
