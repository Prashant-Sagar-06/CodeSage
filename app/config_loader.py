"""
config_loader.py
Reads .codesage.yml from the root of the target repo.
Falls back to sensible defaults if the file doesn't exist.

Example .codesage.yml:
    ignore_files:
      - "*.md"
      - "migrations/*"
    min_score_to_merge: 70
    language: "hi"               # reply language: en, hi, es, fr, de
    slack_notify: true
    slack_channel: "#code-reviews"
    context_aware: true
    max_files: 10
    severity_filter: ["critical", "warning"]  # skip info-level comments
"""

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Optional

import yaml
from github.Repository import Repository

logger = logging.getLogger(__name__)

CONFIG_FILE = ".codesage.yml"


@dataclass
class CodeSageConfig:
    # Files/paths to skip entirely
    ignore_files: list[str] = field(default_factory=list)

    # Minimum score to auto-approve (0 = never auto-approve)
    min_score_to_merge: int = 0

    # Reply language for comments (en, hi, es, fr, de, zh)
    language: str = "en"

    # Slack integration
    slack_notify: bool = True
    slack_channel: str = "#general"

    # Fetch related files for deeper context
    context_aware: bool = True

    # Cap on files reviewed per PR
    max_files: int = 15

    # Which severity levels to post as inline comments
    # (summary always shows everything)
    severity_filter: list[str] = field(
        default_factory=lambda: ["critical", "warning", "info"]
    )

    # Custom review focus (appended to the LLM prompt)
    custom_instructions: str = ""


def load_config(repo: Repository, ref: str = "HEAD") -> CodeSageConfig:
    """
    Fetch and parse .codesage.yml from the repo at the given ref.
    Returns default config if the file doesn't exist or is malformed.
    """
    try:
        file_content = repo.get_contents(CONFIG_FILE, ref=ref)
        raw_yaml = file_content.decoded_content.decode("utf-8")
        data = yaml.safe_load(raw_yaml) or {}
        config = _parse_config(data)
        logger.info(f"Loaded .codesage.yml from {repo.full_name} — {config}")
        return config
    except Exception as e:
        # File doesn't exist or YAML parse error — use defaults silently
        logger.debug(f"No .codesage.yml found in {repo.full_name}, using defaults: {e}")
        return CodeSageConfig()


def _parse_config(data: dict) -> CodeSageConfig:
    """Parse raw YAML dict into a CodeSageConfig, ignoring unknown keys."""
    return CodeSageConfig(
        ignore_files=_as_list(data.get("ignore_files", [])),
        min_score_to_merge=int(data.get("min_score_to_merge", 0)),
        language=str(data.get("language", "en")).lower().strip(),
        slack_notify=bool(data.get("slack_notify", True)),
        slack_channel=str(data.get("slack_channel", "#general")),
        context_aware=bool(data.get("context_aware", True)),
        max_files=int(data.get("max_files", 15)),
        severity_filter=_as_list(
            data.get("severity_filter", ["critical", "warning", "info"])
        ),
        custom_instructions=str(data.get("custom_instructions", "")),
    )


def _as_list(value) -> list:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return []


def should_ignore_file(filename: str, ignore_patterns: list[str]) -> bool:
    """Return True if the filename matches any ignore pattern."""
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(filename, pattern):
            return True
        # Also match just the filename part (not full path)
        basename = filename.split("/")[-1]
        if fnmatch.fnmatch(basename, pattern):
            return True
    return False