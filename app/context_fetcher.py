"""
context_fetcher.py
Feature #9 — Context-aware reviews.

For each changed file, fetches related files from the repo so the LLM
understands the full picture — not just the isolated diff.

Strategy:
  1. Same-directory siblings (same module/package)
  2. Files that import or are imported by the changed file
  3. Test file for the changed file (if exists)
  4. Cap total context to avoid blowing the LLM context window
"""

import logging
import re
from typing import Optional

from github.PullRequest import PullRequest
from github.Repository import Repository

logger = logging.getLogger(__name__)

# Max characters of context to attach per file
MAX_CONTEXT_CHARS = 3000
# Max related files to fetch per changed file
MAX_RELATED_FILES = 3


def fetch_file_context(
    repo: Repository,
    changed_filename: str,
    pr: PullRequest,
) -> dict[str, str]:
    """
    Fetch related file contents for a changed file.

    Returns a dict of {filename: content_snippet} for context.
    """
    related: dict[str, str] = {}
    ref = pr.head.sha

    # 1. Try to fetch the test file
    test_path = _find_test_path(changed_filename)
    if test_path:
        content = _safe_fetch(repo, test_path, ref)
        if content:
            related[test_path] = _truncate(content)

    # 2. Fetch same-directory __init__.py for package context
    init_path = _find_init_path(changed_filename)
    if init_path and init_path != changed_filename:
        content = _safe_fetch(repo, init_path, ref)
        if content:
            related[init_path] = _truncate(content, max_chars=1000)

    # 3. Find files that this file imports (Python-specific)
    if changed_filename.endswith(".py"):
        try:
            current_content = _safe_fetch(repo, changed_filename, ref)
            if current_content:
                imports = _extract_local_imports(current_content, changed_filename)
                for imp_path in imports[:MAX_RELATED_FILES - len(related)]:
                    if imp_path not in related:
                        content = _safe_fetch(repo, imp_path, ref)
                        if content:
                            related[imp_path] = _truncate(content)
        except Exception as e:
            logger.debug(f"Could not extract imports from {changed_filename}: {e}")

    logger.info(
        f"Context for {changed_filename}: fetched {len(related)} related files "
        f"({list(related.keys())})"
    )
    return related


def format_context_for_prompt(related_files: dict[str, str]) -> str:
    """Format related file contents into a prompt-ready string."""
    if not related_files:
        return ""

    lines = ["\n\nRELATED FILES (for context — do not review these, use for understanding only):"]
    for filename, content in related_files.items():
        lines.append(f"\n--- {filename} ---\n{content}")

    return "\n".join(lines)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_fetch(repo: Repository, path: str, ref: str) -> Optional[str]:
    """Fetch a file's content from GitHub, returning None if not found."""
    try:
        file_obj = repo.get_contents(path, ref=ref)
        if hasattr(file_obj, "decoded_content"):
            return file_obj.decoded_content.decode("utf-8", errors="replace")
    except Exception:
        pass
    return None


def _truncate(content: str, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Truncate content and add a note if it was cut."""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"\n... (truncated, {len(content)} total chars)"


def _find_test_path(filename: str) -> Optional[str]:
    """
    Guess the test file path for a given source file.
    e.g. app/utils.py → tests/test_utils.py
         src/models/user.py → tests/test_user.py
    """
    parts = filename.split("/")
    basename = parts[-1]

    if not basename.endswith(".py") or basename.startswith("test_"):
        return None

    stem = basename[:-3]  # remove .py
    test_name = f"test_{stem}.py"

    # Try common test directory patterns
    candidates = [
        f"tests/{test_name}",
        f"test/{test_name}",
        "/".join(parts[:-1] + [test_name]),   # same directory
    ]
    return candidates[0]  # return most likely candidate


def _find_init_path(filename: str) -> Optional[str]:
    """Return the __init__.py for the same package."""
    parts = filename.split("/")
    if len(parts) <= 1:
        return None
    directory = "/".join(parts[:-1])
    return f"{directory}/__init__.py"


def _extract_local_imports(source: str, current_file: str) -> list[str]:
    """
    Parse Python source for relative/local imports and resolve to file paths.
    e.g. `from app.utils import helper` → app/utils.py
    """
    paths = []
    current_dir = "/".join(current_file.split("/")[:-1])

    # Match: from X import Y  or  import X
    patterns = [
        r"^from\s+([\w.]+)\s+import",
        r"^import\s+([\w.]+)",
    ]

    for line in source.split("\n"):
        line = line.strip()
        for pattern in patterns:
            m = re.match(pattern, line)
            if m:
                module = m.group(1)
                # Only resolve local (non-stdlib) imports
                if "." in module or _looks_local(module, current_dir):
                    path = module.replace(".", "/") + ".py"
                    if path != current_file:
                        paths.append(path)
                break

    return list(dict.fromkeys(paths))  # deduplicate, preserve order


def _looks_local(module: str, current_dir: str) -> bool:
    """Heuristic: module name starts with same top-level package as current file."""
    if not current_dir:
        return False
    top_package = current_dir.split("/")[0]
    return module.startswith(top_package)