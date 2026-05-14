from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from agent.graph_agent import GraphAgent


@dataclass
class AgentSession:
    agent: GraphAgent = field(default_factory=GraphAgent)
    lock: Lock = field(default_factory=Lock)


class AgentSessionStore:
    """In-memory session store for browser conversations.

    GraphAgent keeps its own message history and active_requirement, so each
    browser session must reuse the same GraphAgent instance across turns.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._store_lock = Lock()

    def _get_session(self, session_id: str) -> AgentSession:
        with self._store_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = AgentSession()
            return self._sessions[session_id]

    async def chat(self, session_id: str, message: str) -> dict[str, Any]:
        session = self._get_session(session_id)

        def run_locked() -> dict[str, Any]:
            with session.lock:
                answer = session.agent.run(message)
                return {
                    "session_id": session_id,
                    "answer": answer,
                    "active_requirement": session.agent.active_requirement,
                }

        return await asyncio.to_thread(run_locked)

    async def reset(self, session_id: str) -> dict[str, Any]:
        session = self._get_session(session_id)

        def reset_locked() -> dict[str, Any]:
            with session.lock:
                session.agent.reset()
                return {
                    "session_id": session_id,
                    "message": "session reset",
                    "active_requirement": None,
                }

        return await asyncio.to_thread(reset_locked)


session_store = AgentSessionStore()
