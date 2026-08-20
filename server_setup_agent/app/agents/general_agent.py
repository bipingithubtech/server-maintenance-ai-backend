"""
General Agent - Handles general questions, explanations, and knowledge queries.

This agent is for questions that don't require server actions, like:
- "How does PM2 work?"
- "What is nginx?"
- "Explain Docker to me"
- "How to stop a PM2 instance?"
- Educational/learning questions about server management
"""

from app.services.llm_service import LLMService
from loguru import logger


class GeneralAgent:
    """
    Agent for answering general knowledge questions about server management,
    development tools, and technology concepts.
    """
    
    def __init__(self):
        self.llm = LLMService()
    
    def execute_task(self, query: str) -> str:
        """
        Answer general questions using the LLM's knowledge.
        
        Args:
            query: User's question
            
        Returns:
            Answer from the LLM
        """
        logger.info(f"[GENERAL] Answering question: {query[:100]}...")
        
        # System prompt for general agent
        system_prompt = """You are a helpful server management assistant with expertise in:
- Linux server administration
- PM2 process management
- Nginx web server
- Docker and containerization
- SSH and security
- Node.js, Python, and web deployment
- DevOps best practices

Your role is to:
1. Answer questions clearly and concisely
2. Provide practical examples when helpful
3. Explain concepts in an easy-to-understand way
4. Give step-by-step instructions when asked "how to" questions
5. Recommend best practices

Keep answers focused and practical. If the user asks "how to" do something, provide the actual commands they can use.

Be conversational and helpful. You're teaching someone about server management."""

        # Get answer from LLM
        try:
            response = self.llm.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                temperature=0.7  # Slightly higher for more conversational responses
            )
            
            answer = response.strip()
            logger.info(f"[GENERAL] Answered successfully ({len(answer)} chars)")
            return answer
            
        except Exception as e:
            logger.error(f"[GENERAL] Error generating answer: {e}")
            return f"I encountered an error while trying to answer your question: {str(e)}"
