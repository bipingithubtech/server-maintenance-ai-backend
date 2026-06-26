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
        for attempt in range(max_retries):
            try:
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "413" in err_str or "rate_limit_exceeded" in err_str:
                    # 413 = request too large — retrying won't help
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

        # Try seconds only: "1.5s"
        m = re.search(r'([\d.]+)s', error_message)
        if m:
            return float(m.group(1)) + 1.0

        # Try milliseconds: "130ms"
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
