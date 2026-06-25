"""
app/agents/supervisor.py

Supervisor Agent

Responsibilities:
- Understand user intent
- Route request to the correct specialized agent
- Never execute commands directly
"""

from typing import Literal, TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from app.services.llm_service import get_llm
from app.prompts.supervisor_prompt import SUPERVISOR_PROMPT


# ==========================================================
# Supported Routes
# ==========================================================

AgentRoute = Literal[
    "setup",
    "deployment",
    "security",
    "monitoring",
    "troubleshooting",
    "maintenance",
    "general"
]



class SupervisorDecision(TypedDict):
    agent: AgentRoute
    reason: str

class SupervisorAgent:
    """
    Main routing agent.
    """

    def __init__(self):
        self.llm = get_llm()

        self.prompt = ChatPromptTemplate.from_template(
            SUPERVISOR_PROMPT
        )

        self.parser = JsonOutputParser()

        self.chain = (
            self.prompt
            | self.llm
            | self.parser
        )

    def route(self, query: str) -> SupervisorDecision:
        """
        Decide which agent should handle the request.

        Args:
            query: User request

        Returns:
            dict containing selected agent and reason
        """

        try:
            result = self.chain.invoke(
                {"query": query}
            )

            return {
                "agent": result.get("agent", "general"),
                "reason": result.get(
                    "reason",
                    "No reason provided."
                )
            }

        except Exception as exc:
            return {
                "agent": "general",
                "reason": f"Supervisor failed: {str(exc)}"
            }

supervisor = SupervisorAgent()
