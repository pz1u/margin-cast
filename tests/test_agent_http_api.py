import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from src.agent_audit import AgentAuditStore
from src.agent_chat_service import AgentChatService, MemorySessionStore
from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService
from src.generate_data import generate_dataset, save_dataset
from src.http_api import create_server, dispatch_api
from src.mock_llm_provider import MockLLMProvider
from src.prepare_analysis_data import prepare_analysis_data


class SequenceIdFactory:
    def __init__(self, prefix):
        self.prefix = prefix
        self.position = 0

    def __call__(self):
        self.position += 1
        return f"{self.prefix}-{self.position}"


class RecordingExecutor:
    def __init__(self, service):
        self.service = service
        self.calls = []

    def __call__(self, tool_name, arguments):
        raw = execute_tool(tool_name, arguments, service=self.service)
        self.calls.append((tool_name, arguments, raw))
        return raw


class FailingProvider:
    model = "failing-test-model"
    prompt_version = "failing-test-v1"

    def generate(self, messages, tools):
        raise RuntimeError("internal stack api_key=secret-value")


class AgentHttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine_directory = tempfile.TemporaryDirectory()
        root = Path(cls.engine_directory.name)
        tables, truth = generate_dataset()
        save_dataset(tables, truth, root)
        panel_path = root / "processed" / "demand_panel.csv"
        prepare_analysis_data(root, panel_path)
        (root / "ground_truth.json").unlink()
        cls.engine = MarginCastDecisionService(panel_path)

    @classmethod
    def tearDownClass(cls):
        cls.engine_directory.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.session_store = MemorySessionStore()
        self.executor = RecordingExecutor(self.engine)
        self.execution_ids = SequenceIdFactory("execution")

    def tearDown(self):
        self.temporary.cleanup()

    def chat_service(self, provider_factory=lambda: MockLLMProvider()):
        return AgentChatService(
            provider_factory=provider_factory,
            tool_executor=self.executor,
            capabilities_loader=self.engine.get_capabilities,
            audit_store=AgentAuditStore(
                Path(self.temporary.name) / "agent-audit.json"
            ),
            session_store=self.session_store,
            session_id_factory=SequenceIdFactory("session"),
            execution_id_factory=self.execution_ids,
        )

    @staticmethod
    def request(service, message, session_id=None):
        body = {"message": message}
        if session_id is not None:
            body["session_id"] = session_id
        return dispatch_api(
            "POST",
            "/api/agent/chat",
            body,
            agent_chat_service=service,
        )

    def test_completed_response_uses_engine_facts_without_tool_result(self):
        status, payload = self.request(
            self.chat_service(),
            "치킨마요를 9,500원으로 올리면 어때?",
            "session-completed",
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertTrue(payload["execution_id"])
        self.assertTrue(payload["recommendation_id"])
        self.assertIn("facts", payload)
        self.assertIn("presentation", payload)
        self.assertIn("notices", payload)
        self.assertNotIn("tool_result", payload)
        self.assertNotIn("messages", payload)
        self.assertNotIn("strategies", payload)

        raw = self.executor.calls[-1][2]
        selected_id = raw["recommended_action"]["scenario_id"]
        selected = next(
            strategy
            for strategy in raw["strategies"]
            if strategy["scenario_id"] == selected_id
        )
        expected = {
            "selected_scenario_id": selected_id,
            "expected_units": selected["units"]["mean"],
            "expected_contribution_profit": selected["contribution_profit"]["mean"],
            "profit_delta": selected["profit_delta"]["mean"],
            "interval_80": {
                "lower": selected["profit_delta"]["p10"],
                "upper": selected["profit_delta"]["p90"],
            },
            "success_probability": selected["success_probability"],
            "downside_risk": selected["downside_risk"],
            "evidence_quality": selected["evidence_quality"],
            "engine_decision": raw["recommended_action"]["action"],
            "data_provenance": raw["data_provenance"],
        }
        self.assertEqual(
            {name: fact["value"] for name, fact in payload["facts"].items()},
            expected,
        )
        self.assertEqual(
            payload["presentation"]["explanation"],
            "합성 데이터 결과이며 근거 품질은 아직 미보정 상태입니다.",
        )
        self.assertNotEqual(payload["presentation"], payload["facts"])

    def test_missing_price_continues_with_same_session(self):
        service = self.chat_service()
        first_status, first = self.request(
            service,
            "치킨마요 가격 올리면 어때?",
            "session-two-turn",
        )
        second_status, second = self.request(
            service,
            "9500원",
            "session-two-turn",
        )

        self.assertEqual((first_status, first["status"]), (200, "NEEDS_INPUT"))
        self.assertEqual(
            first["question"],
            "변경할 가격은 얼마로 생각하고 계신가요?",
        )
        self.assertEqual(first["missing_input"]["fields"], ["list_price"])
        self.assertEqual((second_status, second["status"]), (200, "COMPLETED"))
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(
            self.executor.calls[0][1]["scenarios"][0]["list_price"],
            9500,
        )
        state = self.session_store.get("session-two-turn")
        self.assertIsNone(state.pending_question)
        self.assertEqual(state.selected_menu["menu_id"], "M01")
        self.assertEqual(state.draft_scenario["list_price"], 9500)
        self.assertEqual(state.recommendation_id, second["recommendation_id"])
        self.assertNotIn("치킨마요 가격 올리면 어때?", json.dumps(state.__dict__, ensure_ascii=False))

    def test_sessions_do_not_share_pending_context(self):
        service = self.chat_service()
        self.request(service, "치킨마요 가격 올리면 어때?", "session-a")
        status, payload = self.request(service, "9500원", "session-b")

        self.assertEqual((status, payload["status"]), (200, "NEEDS_INPUT"))
        self.assertEqual(payload["missing_input"]["fields"], ["menu_id"])
        self.assertIsNotNone(self.session_store.get("session-a").selected_menu)
        self.assertIsNone(self.session_store.get("session-b").selected_menu)
        self.assertEqual(self.executor.calls, [])

    def test_rejected_response_is_structured_without_llm_text(self):
        service = self.chat_service(
            lambda: MockLLMProvider(
                final_text="합성 데이터이며 근거 품질은 미보정 상태입니다. 7"
            )
        )
        status, payload = self.request(
            service,
            "치킨마요를 9,500원으로 올리면 어때?",
            "session-rejected",
        )

        self.assertEqual((status, payload["status"]), (200, "REJECTED"))
        self.assertIsNone(payload["recommendation_id"])
        self.assertIn(
            "LLM_EXPLANATION_CONTAINS_NUMBER",
            payload["policy_validation"]["violation_codes"],
        )
        self.assertNotIn("presentation", payload)
        self.assertTrue(payload["execution_id"])

    def test_error_does_not_expose_internal_exception_or_stack(self):
        status, payload = self.request(
            self.chat_service(lambda: FailingProvider()),
            "치킨마요를 9,500원으로 올리면 어때?",
            "session-error",
        )
        serialized = json.dumps(payload, ensure_ascii=False).lower()

        self.assertEqual((status, payload["status"]), (500, "ERROR"))
        self.assertTrue(payload["execution_id"])
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("stack", serialized)
        self.assertNotIn("traceback", serialized)

    def test_response_and_session_exclude_sensitive_request_text(self):
        message = (
            "치킨마요를 9,500원으로 올리면 어때? "
            "api_key=secret 서울 중구 세종대로 110 latitude=37.5 longitude=127.0"
        )
        status, payload = self.request(
            self.chat_service(),
            message,
            "session-sensitive",
        )
        state = self.session_store.get("session-sensitive")
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        state_text = json.dumps(state.__dict__, ensure_ascii=False).lower()

        self.assertEqual((status, payload["status"]), (200, "COMPLETED"))
        for forbidden in (
            "secret",
            "세종대로",
            "latitude",
            "longitude",
        ):
            self.assertNotIn(forbidden, serialized)
            self.assertNotIn(forbidden, state_text)

    def test_real_http_server_completes_agent_request(self):
        service = self.chat_service()
        with tempfile.TemporaryDirectory() as directory:
            static_dir = Path(directory)
            (static_dir / "index.html").write_text("ok", encoding="utf-8")
            server = create_server(
                "127.0.0.1",
                0,
                service=self.engine,
                static_dir=static_dir,
                agent_chat_service=service,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            try:
                body = json.dumps(
                    {
                        "session_id": "session-http",
                        "message": "치킨마요를 9,500원으로 올리면 어때?",
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                connection.request(
                    "POST",
                    "/api/agent/chat",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["session_id"], "session-http")


if __name__ == "__main__":
    unittest.main()
