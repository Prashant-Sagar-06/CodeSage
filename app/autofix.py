"""
autofix.py
Feature #3 — Auto-fix suggestions as GitHub suggestion blocks.

GitHub supports a special markdown syntax in review comments:
    ```suggestion
    corrected code here
    ```
When posted on a specific diff line, GitHub renders a "Accept suggestion"
button that applies the fix with one click.

This module:
  1. Asks the LLM for a concrete single-line or multi-line fix
  2. Formats it as a GitHub suggestion block
  3. Returns the full comment body ready to post
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def build_suggestion_comment(
    original_line: str,
    suggestion_text: str,
    severity: str,
    title: str,
    description: str,
) -> str:
    """
    Build a GitHub review comment body that includes a suggestion block.

    GitHub renders ```suggestion blocks as one-click-apply patches.
    The suggestion must contain the REPLACEMENT for the target line(s).
    """
    icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")
    severity_label = severity.upper()

    # Clean the suggestion — remove diff markers if LLM included them
    clean_suggestion = _clean_suggestion(suggestion_text, original_line)

    body = f"{icon} **[{severity_label}] {title}**\n\n"
    body += f"{description}\n\n"

    if clean_suggestion:
        body += f"```suggestion\n{clean_suggestion}\n```\n\n"

    body += "*CodeSage AI · Powered by Groq llama-3.3-70b*"
    return body


def extract_code_fix(suggestion_text: str) -> Optional[str]:
    """
    Extract clean code from a suggestion string.
    Handles cases where the LLM wraps code in backticks or adds explanations.
    """
    if not suggestion_text:
        return None

    # If it's a code block, extract just the code
    code_block = re.search(r"```(?:\w+)?\n?(.*?)```", suggestion_text, re.DOTALL)
    if code_block:
        return code_block.group(1).strip()

    # If it's a short single line (likely just the fix)
    lines = [l.strip() for l in suggestion_text.strip().split("\n") if l.strip()]
    if len(lines) <= 3:
        return "\n".join(lines)

    # Take the first code-looking line
    for line in lines:
        if any(c in line for c in ["=", "(", "def ", "return ", "if ", "import "]):
            return line

    return suggestion_text.strip()


def _clean_suggestion(suggestion: str, original_line: str) -> str:
    """
    Clean up the suggestion text for use in a GitHub suggestion block.

    - Strip leading +/- diff markers
    - Preserve original indentation
    - Remove explanatory prose (keep only code)
    """
    if not suggestion:
        return original_line.lstrip("+-").rstrip("\n")

    # Extract code portion
    code = extract_code_fix(suggestion)
    if not code:
        return original_line.lstrip("+-").rstrip("\n")

    # Detect original indentation and apply it
    original_stripped = original_line.lstrip("+-")
    indent = len(original_stripped) - len(original_stripped.lstrip())
    indent_str = " " * indent

    # Apply indent to each line of the suggestion
    lines = code.split("\n")
    indented_lines = []
    for line in lines:
        if line.strip():
            # Only add indent if not already indented
            if not line.startswith(indent_str):
                line = indent_str + line.lstrip()
        indented_lines.append(line)

    return "\n".join(indented_lines)


def has_actionable_fix(suggestion: str) -> bool:
    """
    Returns True if the suggestion contains an actual code fix
    (not just prose advice).
    """
    if not suggestion:
        return False

    # Check for code indicators
    code_indicators = [
        "```",           # code block
        "def ",          # function definition
        "return ",       # return statement
        "if ",           # conditional
        "import ",       # import
        " = ",           # assignment
        "raise ",        # exception
        "->",            # type hint
    ]

    return any(indicator in suggestion for indicator in code_indicators)