"""
conversation_service.py

Manages multi-turn conversations between frontend and agents.

When an agent needs more input (username, password, SSH key etc.),
it raises NeedsInputError instead of calling input().
The API catches this, stores the pending state, and returns a question
to the frontend. The frontend answers, sends the answer back with the
conversation_id, and the agent resumes.
"""

import uuid
import time
from typing import Any, Dict, Optional


class NeedsInputError(Exception):
    """
    Raised by an agent when it needs more information from the user.
    The API catches this and returns a question to the frontend.
    """
    def __init__(self, question: str, state: Dict[str, Any]):
        super().__init__(question)
        self.question = question
        self.state    = state  # everything needed to resume the conversation


class ConversationStore:
    """
    In-memory store for pending conversations.
    Each entry lives for 10 minutes then expires.
    """
    TTL = 600  # seconds

    def __init__(self):
        self._store: Dict[str, Dict] = {}

    def save(self, state: Dict[str, Any]) -> str:
        """Save conversation state, return a unique conversation_id."""
        cid = str(uuid.uuid4())
        self._store[cid] = {
            "state":      state,
            "created_at": time.time(),
        }
        self._cleanup()
        return cid

    def get(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve and remove conversation state by ID."""
        entry = self._store.pop(conversation_id, None)
        if entry is None:
            return None
        # Check TTL
        if time.time() - entry["created_at"] > self.TTL:
            return None
        return entry["state"]

    def _cleanup(self):
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, v in self._store.items()
                   if now - v["created_at"] > self.TTL]
        for k in expired:
            del self._store[k]


# Singleton
conversation_store = ConversationStore()
