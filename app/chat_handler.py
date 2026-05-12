"""
chat_handler.py
Feature #1 — PR Chat. Handles @codesage mentions in PR comments.

When someone writes "@codesage explain this more" or "@codesage how do I fix this?"
on a PR, this handler:
  1. Detects the mention
  2. Fetches the surrounding diff context
  3. Calls the LLM with the question + context
  4. Posts the reply as a PR comment

Supported commands:
  @codesage explain             — explain the issue in more detail
  @codesage how do I fix this?  — give a step-by-step fix
  @codesage is this a false positive? — reconsider the finding
  @codesage <any question>      — general question about the code
"""

import logging
import os
import re
from typing import Optional

from github.IssueComment import IssueComment
from github.PullRequest import PullRequest
from groq import AsyncGroq

logger = logging.getLogger(__name__)

MENTION_PATTERN = re.compile(r"@codesage\b", re.IGNORECASE)
MODEL = "llama-3.3-70b-versatile"

CHAT_SYSTEM_PROMPT = """You are CodeSage — an expert code reviewer assistant.
A developer is asking you a follow-up question about a code review comment on their PR.
Be helpful, specific, and concise. Show code examples where relevant.
Keep your response under 400 words. Use markdown formatting."""

# Language-specific system prompt suffixes
LANGUAGE_INSTRUCTIONS = {
    "hi": "Respond in Hindi (हिंदी में जवाब दें).",
    "es": "Responde en español.",
    "fr": "Réponds en français.",
    "de": "Antworte auf Deutsch.",
    "zh": "用中文回复。",
    "en": "",
}


class ChatHandler:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        self.client = AsyncGroq(api_key=api_key)

    def is_codesage_mention(self, comment_body: str) -> bool:
        """Return True if the comment contains @codesage."""
        return bool(MENTION_PATTERN.search(comment_body or ""))

    def extract_question(self, comment_body: str) -> str:
        """Strip the @codesage mention and return the clean question."""
        question = MENTION_PATTERN.sub("", comment_body).strip()
        return question if question else "Can you explain this issue in more detail?"

    async def handle_comment(
        self,
        pr: PullRequest,
        comment: IssueComment,
        language: str = "en",
    ) -> Optional[str]:
        """
        Generate a reply to a @codesage mention.

        Returns the reply text, or None if it fails.
        """
        question = self.extract_question(comment.body)
        pr_context = self._build_pr_context(pr)
        reply = await self._ask_llm(question, pr_context, language)

        if reply:
            logger.info(
                f"ChatHandler replied to comment on PR #{pr.number} "
                f"(question: '{question[:60]}...')"
            )
        return reply

    async def _ask_llm(
        self,
        question: str,
        pr_context: str,
        language: str = "en",
    ) -> Optional[str]:
        """Send the question + context to the LLM and return the response."""
        lang_instruction = LANGUAGE_INSTRUCTIONS.get(language, "")
        system = CHAT_SYSTEM_PROMPT
        if lang_instruction:
            system += f"\n\n{lang_instruction}"

        user_content = f"""PR Context:
{pr_context}

Developer's question:
{question}

Please answer helpfully and specifically."""

        try:
            response = await self.client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=600,
                temperature=0.3,
            )
            raw = response.choices[0].message.content.strip()
            # Wrap in a CodeSage reply format
            return self._format_reply(raw)
        except Exception as e:
            logger.error(f"ChatHandler LLM call failed: {e}")
            return None

    def _format_reply(self, llm_response: str) -> str:
        """Wrap the LLM response in a CodeSage reply block."""
        return (
            f"### 🧠 CodeSage Reply\n\n"
            f"{llm_response}\n\n"
            f"---\n"
            f"*CodeSage AI · Powered by Groq llama-3.3-70b · "
            f"[Ask another question by mentioning @codesage]*"
        )

    def _build_pr_context(self, pr: PullRequest) -> str:
        """Build a compact context string about the PR for the LLM."""
        try:
            files = [f.filename for f in pr.get_files()]
            file_list = ", ".join(files[:10])
            if len(files) > 10:
                file_list += f" (+{len(files) - 10} more)"

            return (
                f"PR #{pr.number}: {pr.title}\n"
                f"Author: {pr.user.login}\n"
                f"Base branch: {pr.base.ref}\n"
                f"Files changed: {file_list}\n"
                f"Description: {(pr.body or 'No description')[:300]}"
            )
        except Exception as e:
            logger.warning(f"Could not build PR context: {e}")
            return f"PR #{pr.number}: {pr.title}"