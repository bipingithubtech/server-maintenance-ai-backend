import time
import re
from langchain_groq import ChatGroq
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from loguru import logger
from app.core.config import settings

class RateLimitAwareChatGroq(ChatGroq):
    """
    ChatGroq subclass that automatically retries on 429 rate limit errors.
    Parses the Groq retry-after time from the error message and waits.
    """

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        max_retries = 5
        current_messages = list(messages)

        for attempt in range(max_retries):
            try:
                return super()._generate(current_messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as e:
                err_str = str(e)

                # ── 400 tool_use_failed: Groq generated malformed tool XML ──
                # This happens when conversation history is too long / complex.
                # Fix: progressively trim middle messages and retry immediately.
                if "400" in err_str and ("tool_use_failed" in err_str or "Failed to call a function" in err_str):
                    if attempt < max_retries - 1:
                        # Keep system + first user + last N messages, shrinking each retry
                        keep_recent = max(2, 6 - attempt * 2)
                        if len(current_messages) > keep_recent + 2:
                            current_messages = current_messages[:2] + current_messages[-keep_recent:]
                            logger.warning(
                                f"Groq tool_use_failed (400). Trimming history to "
                                f"{len(current_messages)} messages and retrying "
                                f"(attempt {attempt + 1}/{max_retries})..."
                            )
                            continue
                    raise

                # ── 429 / rate limit ──────────────────────────────────────
                if "429" in err_str or "413" in err_str or "rate_limit_exceeded" in err_str:
                    if "413" in err_str or "Request too large" in err_str:
                        raise
                    wait_seconds = self._parse_wait_time(err_str)
                    wait_seconds = max(wait_seconds, 5.0)
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Groq rate limit hit. Waiting {wait_seconds:.1f}s before retry "
                            f"(attempt {attempt + 1}/{max_retries})..."
                        )
                        time.sleep(wait_seconds)
                        continue
                raise

    @staticmethod
    def _parse_wait_time(error_message: str) -> float:
        """
        Extracts wait time in seconds from Groq's rate limit error message.
        Handles formats like: '130ms', '3m13.5s', '26m21s', '0.5s'
        Defaults to 5 seconds if parsing fails.
        """
        # Try minutes + seconds: "3m13.535s"
        m = re.search(r'(\d+)m([\d.]+)s', error_message)
        if m:
            return int(m.group(1)) * 60 + float(m.group(2)) + 1.0

       
        m = re.search(r'([\d.]+)s', error_message)
        if m:
            return float(m.group(1)) + 1.0

       
        m = re.search(r'(\d+)ms', error_message)
        if m:
            return int(m.group(1)) / 1000.0 + 0.5

        return 5.0  # safe default


def get_llm():
    """Return configured LLM instance with automatic rate-limit retry."""
    if settings.LLM_PROVIDER == "groq":
        return RateLimitAwareChatGroq(
            api_key=settings.GROQ_API_KEY,
            model_name=settings.MODEL_NAME,
            temperature=settings.TEMPERATURE,
        
        )

    raise ValueError(f"Unsupported provider: {settings.LLM_PROVIDER}")
