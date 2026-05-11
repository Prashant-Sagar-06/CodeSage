"""
prompt_builder.py
Builds structured prompts for the LLM code reviewer.
Each file diff is sent individually to keep prompts focused
and avoid context window overflow.
"""

SYSTEM_PROMPT = """You are CodeSage — an expert senior software engineer and code reviewer.
You review code diffs from GitHub Pull Requests.
You identify bugs, security vulnerabilities, performance issues, and style problems.

CRITICAL RULES:
- Return ONLY valid JSON. No markdown. No prose. No backticks.
- Every comment must reference a real line number from the diff.
- Be specific and actionable — generic advice is useless.
- severity must be exactly one of: "critical", "warning", "info"
- score must be an integer from 0 to 100

JSON SCHEMA (return exactly this structure):
{
  "comments": [
    {
      "line": <integer>,
      "severity": "critical" | "warning" | "info",
      "title": "<short title, max 60 chars>",
      "description": "<detailed explanation of the issue>",
      "suggestion": "<concrete code fix or recommendation>"
    }
  ],
  "optimizations": ["<optimization suggestion>"],
  "complexity": "<low | medium | high>",
  "score": <0-100>,
  "language": "<detected programming language>"
}"""


def build_file_prompt(
    filename: str,
    file_diff: str,
    pr_title: str,
    pr_description: str = "",
    detected_language: str = "unknown",
) -> list[dict]:
    """
    Build the messages array for a single file review.

    Returns a list of message dicts ready for the Groq API.
    """
    user_content = f"""File: {filename}
Language: {detected_language}
PR Title: {pr_title}
PR Description: {pr_description or "No description provided"}

CODE DIFF:
{file_diff}

Review this diff carefully. Focus on:
1. Bugs and logic errors (critical)
2. Security vulnerabilities — injection, auth issues, secrets (critical)  
3. Performance problems — N+1 queries, unnecessary loops (warning)
4. Error handling gaps — missing try/except, unhandled edge cases (warning)
5. Code quality — naming, readability, maintainability (info)
6. Missing type hints or docstrings (info)

Return ONLY the JSON object. Nothing else."""

    return [
        {"role": "user", "content": user_content}
    ]


def detect_language(filename: str) -> str:
    """Detect programming language from file extension."""
    ext_map = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript/React",
        ".jsx": "JavaScript/React",
        ".java": "Java",
        ".go": "Go",
        ".rs": "Rust",
        ".cpp": "C++",
        ".c": "C",
        ".cs": "C#",
        ".rb": "Ruby",
        ".php": "PHP",
        ".swift": "Swift",
        ".kt": "Kotlin",
        ".scala": "Scala",
        ".sh": "Shell",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".json": "JSON",
        ".sql": "SQL",
        ".tf": "Terraform",
        ".md": "Markdown",
    }
    for ext, lang in ext_map.items():
        if filename.endswith(ext):
            return lang
    return "Unknown"
