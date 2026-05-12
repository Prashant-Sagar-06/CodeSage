"""
prompt_builder.py
Updated for context-aware reviews, custom instructions, and multilingual output.
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
- suggestion must contain actual corrected code where possible (not just prose)

JSON SCHEMA (return exactly this structure):
{
  "comments": [
    {
      "line": <integer>,
      "severity": "critical" | "warning" | "info",
      "title": "<short title, max 60 chars>",
      "description": "<detailed explanation of the issue>",
      "suggestion": "<corrected code line(s) or concrete fix>"
    }
  ],
  "optimizations": ["<optimization suggestion>"],
  "complexity": "<low | medium | high>",
  "score": <0-100>,
  "language": "<detected programming language>"
}"""

LANGUAGE_INSTRUCTIONS = {
    "hi": "\n\nIMPORTANT: Write all 'title', 'description', and 'suggestion' fields in Hindi (हिंदी). Keep code/variable names in English.",
    "es": "\n\nIMPORTANT: Write all 'title', 'description', and 'suggestion' fields in Spanish.",
    "fr": "\n\nIMPORTANT: Write all 'title', 'description', and 'suggestion' fields in French.",
    "de": "\n\nIMPORTANT: Write all 'title', 'description', and 'suggestion' fields in German.",
    "zh": "\n\nIMPORTANT: Write all 'title', 'description', and 'suggestion' fields in Chinese (中文).",
    "en": "",
}


def get_system_prompt(language: str = "en") -> str:
    lang_suffix = LANGUAGE_INSTRUCTIONS.get(language, "")
    return SYSTEM_PROMPT + lang_suffix


def build_file_prompt(
    filename: str,
    file_diff: str,
    pr_title: str,
    pr_description: str = "",
    detected_language: str = "unknown",
    related_context: str = "",
    custom_instructions: str = "",
) -> list[dict]:
    user_content = f"""File: {filename}
Language: {detected_language}
PR Title: {pr_title}
PR Description: {pr_description or "No description provided"}

CODE DIFF:
{file_diff}"""

    if related_context:
        user_content += related_context

    user_content += """

Review this diff carefully. Focus on:
1. Bugs and logic errors (critical)
2. Security vulnerabilities — injection, auth issues, hardcoded secrets (critical)
3. Performance problems — N+1 queries, unnecessary loops, memory leaks (warning)
4. Error handling gaps — missing try/except, unhandled edge cases (warning)
5. Code quality — naming, readability, maintainability (info)
6. Missing type hints or docstrings (info)

For each comment, provide a concrete corrected version of the code in 'suggestion'.
Return ONLY the JSON object. Nothing else."""

    if custom_instructions:
        user_content += f"\n\nADDITIONAL REVIEW FOCUS:\n{custom_instructions}"

    return [{"role": "user", "content": user_content}]


def detect_language(filename: str) -> str:
    ext_map = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
        ".java": "Java", ".go": "Go", ".rs": "Rust", ".cpp": "C++",
        ".c": "C", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
        ".swift": "Swift", ".kt": "Kotlin", ".scala": "Scala",
        ".sh": "Shell", ".yaml": "YAML", ".yml": "YAML",
        ".json": "JSON", ".sql": "SQL", ".tf": "Terraform",
        ".md": "Markdown", ".html": "HTML", ".css": "CSS",
        ".vue": "Vue", ".dart": "Dart", ".r": "R",
    }
    for ext, lang in ext_map.items():
        if filename.endswith(ext):
            return lang
    return "Unknown"