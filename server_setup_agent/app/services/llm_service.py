from langchain_groq import ChatGroq
from app.core.config import settings

def get_llm():
    """
    Return configured LLM instance.
    """
    if settings.LLM_PROVIDER == "groq":
        return ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model_name=settings.MODEL_NAME,
            temperature=settings.TEMPERATURE,
        )

    raise ValueError(f"Unsupported provider: {settings.LLM_PROVIDER}")
