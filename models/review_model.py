from dataclasses import dataclass, field
from typing import List


@dataclass
class ReviewComment:
    """Represents a single review comment on a specific line in a file."""
    file_path: str
    line: int
    severity: str          # critical / warning / info
    title: str
    description: str
    suggestion: str        # concrete fix recommendation

    def to_github_body(self) -> str:
        """Format comment for posting to GitHub."""
        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(self.severity, "⚪")
        severity_label = f"[{self.severity.upper()}]"
        body = f"{icon} **{severity_label} {self.title}**\n\n"
        body += f"{self.description}\n\n"
        if self.suggestion:
            body += f"**💡 Suggestion:** {self.suggestion}\n"
        body += "\n---\n*CodeSage AI · Powered by Groq llama-3.3-70b*"
        return body


@dataclass
class PRReview:
    """Complete review of a single Pull Request."""
    pr_number: int
    repo_name: str
    score: int             # 0–100
    language: str
    files_reviewed: int
    comments: List[ReviewComment] = field(default_factory=list)
    summary: str = ""      # overall narrative

    @property
    def critical_comments(self) -> List[ReviewComment]:
        return [c for c in self.comments if c.severity == "critical"]

    @property
    def warning_comments(self) -> List[ReviewComment]:
        return [c for c in self.comments if c.severity == "warning"]

    @property
    def info_comments(self) -> List[ReviewComment]:
        return [c for c in self.comments if c.severity == "info"]

    @property
    def label(self) -> str:
        """Determine which label to apply based on score and critical issues."""
        has_security = any(
            "security" in c.title.lower() or "injection" in c.title.lower()
            or "xss" in c.title.lower() or "sql" in c.title.lower()
            for c in self.critical_comments
        )
        if has_security:
            return "codesage: security-risk"
        elif self.score >= 85:
            return "codesage: looks-good"
        else:
            return "codesage: needs-work"

    def to_summary_comment(self) -> str:
        """Build the full markdown summary comment for the PR."""
        score_emoji = "🟢" if self.score >= 85 else "🟡" if self.score >= 40 else "🔴"

        lines = [
            "## 🧠 CodeSage PR Review\n",
            "| | |",
            "|---|---|",
            f"| **Score** | {score_emoji} {self.score}/100 |",
            f"| **Language** | {self.language} |",
            f"| **Files Reviewed** | {self.files_reviewed} |",
            f"| **Critical Bugs** | {len(self.critical_comments)} |",
            f"| **Warnings** | {len(self.warning_comments)} |",
            f"| **Suggestions** | {len(self.info_comments)} |",
            "",
        ]

        if self.critical_comments:
            lines.append("### 🔴 Critical Issues")
            for c in self.critical_comments:
                lines.append(f"- `{c.file_path}` L{c.line} — {c.title}")
            lines.append("")

        if self.warning_comments:
            lines.append("### 🟡 Warnings")
            for c in self.warning_comments:
                lines.append(f"- `{c.file_path}` L{c.line} — {c.title}")
            lines.append("")

        if self.info_comments:
            lines.append("### ⚡ Suggestions")
            for c in self.info_comments:
                lines.append(f"- `{c.file_path}` L{c.line} — {c.title}")
            lines.append("")

        if self.summary:
            lines.append("### 📝 Overall Assessment")
            lines.append(self.summary)
            lines.append("")

        lines.append("---")
        lines.append("*Reviewed by CodeSage AI · Powered by Groq llama-3.3-70b*")

        return "\n".join(lines)
