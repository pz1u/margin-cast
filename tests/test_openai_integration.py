"""실제 OpenAI 가격 Agent 연결. RUN_OPENAI_INTEGRATION=1일 때만 실행한다."""

import os
import unittest

from src.agent_chat_service import AgentChatService, MemorySessionStore
from src.agent_audit import AgentAuditStore
from src.agent_tool_contracts import execute_tool
from src.openai_provider import OpenAIProvider


@unittest.skipUnless(
    os.getenv("RUN_OPENAI_INTEGRATION") == "1",
    "RUN_OPENAI_INTEGRATION=1일 때만 실제 OpenAI 통합 테스트를 실행합니다.",
)
class OpenAIIntegrationTests(unittest.TestCase):
    def test_price_question_reaches_completed_response(self):
        with self.subTest("environment"):
            self.assertTrue(os.getenv("OPENAI_API_KEY"))
            self.assertTrue(os.getenv("OPENAI_MODEL"))

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            service = AgentChatService(
                provider_factory=OpenAIProvider,
                tool_executor=execute_tool,
                capabilities_loader=lambda: execute_tool(
                    "get_margincast_capabilities", {}
                ),
                audit_store=AgentAuditStore(Path(directory) / "audit.json"),
                session_store=MemorySessionStore(),
            )
            status, payload = service.chat(
                "openai-integration",
                "치킨마요를 9,500원으로 올리면 어때?",
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(
            payload["agent_response"]["facts"]["engine_decision"]["value"],
            payload["agent_response"]["presentation"]["decision"],
        )


if __name__ == "__main__":
    unittest.main()