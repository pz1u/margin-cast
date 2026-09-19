from copy import deepcopy
import json
import unittest

from src.agent_runtime import (
    AgentMissingInputError,
    AgentRuntime,
    ProviderInvalidToolCallError,
)
from src.agent_schemas import (
    AgentInput,
    DecisionAction,
    Message,
    MessageRole,
    Provenance,
    ValueSource,
)
from src.agent_tool_contracts import TOOL_SCHEMAS
from src.llm_provider import LLMProvider
from src.ollama_provider import OllamaProvider, OllamaProviderError
from tests.test_agent_runtime import RecordingToolExecutor


class StubResponse:
    def __init__(self, payload):
        self.payload = deepcopy(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return deepcopy(self.payload)


class StubHttpClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, *, json, timeout):
        self.requests.append(
            {"url": url, "json": deepcopy(json), "timeout": timeout}
        )
        return StubResponse(self.responses.pop(0))


def ollama_tool_response(arguments=None, name="compare_price_strategies"):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": arguments
                        if arguments is not None
                        else {
                            "menu_id": "M01",
                            "scenarios": [
                                {
                                    "name": "가격 인상",
                                    "list_price": 9500,
                                    "discount": 0,
                                }
                            ],
                        },
                    },
                }
            ],
        }
    }


def ollama_final_response():
    return {
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {
                    "decision_claim": "EXPERIMENT",
                    "explanation": "합성 데이터 결과이며 근거 품질은 아직 미보정 상태입니다.",
                    "next_action": "작은 범위의 검증을 먼저 준비해주세요.",
                },
                ensure_ascii=False,
            ),
        }
    }


def structured_price_input():
    business_inputs = {
        "menu_id": "M01",
        "scenarios": [
            {"name": "가격 인상", "list_price": 9500, "discount": 0}
        ],
    }
    return AgentInput(
        message_id="ollama-user-1",
        text="치킨마요를 9,500원으로 올리면 어때?",
        business_inputs=business_inputs,
        provenance={
            name: Provenance(ValueSource.USER, f"ollama-user-1:{name}")
            for name in business_inputs
        },
    )


class OllamaProviderTests(unittest.TestCase):
    def provider(self, *responses):
        client = StubHttpClient(*responses)
        provider = OllamaProvider(
            base_url="http://ollama.test:11434",
            model="test-tool-model",
            http_client=client,
        )
        return provider, client

    def test_tool_call_response_converts_to_common_tool_call(self):
        provider, client = self.provider(ollama_tool_response())

        response = provider.generate(
            (Message(MessageRole.USER, "가격을 바꾸면 어때?"),),
            TOOL_SCHEMAS,
        )

        self.assertIsInstance(provider, LLMProvider)
        self.assertEqual(len(response.tool_calls), 1)
        tool_call = response.tool_calls[0]
        self.assertEqual(tool_call.name, "compare_price_strategies")
        self.assertEqual(tool_call.arguments["menu_id"], "M01")
        self.assertEqual(tool_call.arguments["scenarios"][0]["list_price"], 9500)
        payload = client.requests[0]["json"]
        self.assertEqual(payload["model"], "test-tool-model")
        self.assertFalse(payload["stream"])
        self.assertIs(payload["think"], False)
        self.assertNotIn("format", payload)
        system_prompt = payload["messages"][0]["content"]
        self.assertIn("계산하거나 생성하지 마세요", system_prompt)
        self.assertIn("Decision은 변경하거나 재판단하지 말고", system_prompt)
        self.assertIn("성공확률과 근거 품질은 서로 다른 개념", system_prompt)
        self.assertNotIn("10000", system_prompt)
        self.assertNotIn("42", system_prompt)
        self.assertNotIn("14일", system_prompt)
        self.assertEqual(
            payload["tools"][1],
            {
                "type": "function",
                "function": {
                    "name": "compare_price_strategies",
                    "description": TOOL_SCHEMAS[1]["description"],
                    "parameters": TOOL_SCHEMAS[1]["parameters"],
                },
            },
        )

    def test_final_response_converts_to_structured_common_response(self):
        provider, client = self.provider(ollama_final_response())
        messages = (
            Message(MessageRole.USER, "가격을 바꾸면 어때?"),
            Message(
                MessageRole.TOOL,
                '{"status":"ok"}',
                tool_call_id="call-1",
                tool_name="compare_price_strategies",
            ),
        )

        response = provider.generate(messages, TOOL_SCHEMAS)

        self.assertEqual(response.decision_claim, DecisionAction.EXPERIMENT)
        self.assertEqual(
            response.text,
            "합성 데이터 결과이며 근거 품질은 아직 미보정 상태입니다.",
        )
        self.assertEqual(response.next_action, "작은 범위의 검증을 먼저 준비해주세요.")
        self.assertEqual(
            client.requests[0]["json"]["format"]["required"],
            ["decision_claim", "explanation", "next_action"],
        )
        self.assertIn(
            "숫자",
            client.requests[0]["json"]["format"]["properties"]["next_action"][
                "description"
            ],
        )
        self.assertIn(
            "parameters에 정의된 필드만 사용",
            client.requests[0]["json"]["messages"][0]["content"],
        )
        self.assertIn(
            "null로 보내지 말고 생략",
            client.requests[0]["json"]["messages"][0]["content"],
        )
        self.assertIn(
            "문자 체계와 관계없이 숫자나 수사",
            client.requests[0]["json"]["messages"][0]["content"],
        )
        self.assertIn(
            "세 필드만 포함",
            client.requests[0]["json"]["messages"][0]["content"],
        )

    def test_unknown_tool_name_is_rejected(self):
        provider, _ = self.provider(
            ollama_tool_response(name="not_a_margincast_tool")
        )

        with self.assertRaises(OllamaProviderError) as context:
            provider.generate((Message(MessageRole.USER, "질문"),), TOOL_SCHEMAS)

        self.assertEqual(context.exception.code, "PROVIDER_INVALID_TOOL_CALL")

    def test_non_object_tool_arguments_are_rejected(self):
        provider, _ = self.provider(ollama_tool_response(arguments=["M01"]))

        with self.assertRaises(OllamaProviderError) as context:
            provider.generate((Message(MessageRole.USER, "질문"),), TOOL_SCHEMAS)

        self.assertEqual(context.exception.code, "PROVIDER_INVALID_TOOL_CALL")

    def test_runtime_uses_provider_interface_and_only_executor_runs_tool(self):
        provider, client = self.provider(
            ollama_tool_response(),
            ollama_final_response(),
        )
        executor = RecordingToolExecutor(
            {
                "status": "ok",
                "recommended_action": {"action": "EXPERIMENT"},
            }
        )

        result = AgentRuntime(provider, tool_executor=executor).run(
            structured_price_input()
        )

        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(executor.calls), 1)
        first_user_message = client.requests[0]["json"]["messages"][1]
        self.assertIn("구조화된 Conversation State", first_user_message["content"])
        self.assertIn('"menu_id":"M01"', first_user_message["content"])
        self.assertEqual(result.tool_call.name, "compare_price_strategies")
        self.assertEqual(result.final_response.decision_claim, DecisionAction.EXPERIMENT)
        self.assertEqual(
            result.final_response.next_action,
            "작은 범위의 검증을 먼저 준비해주세요.",
        )

    def test_omitted_argument_already_in_state_is_provider_contract_error(self):
        provider, _ = self.provider(
            ollama_tool_response(arguments={"menu_id": "M01"})
        )
        executor = RecordingToolExecutor({"status": "ok"})

        with self.assertRaises(ProviderInvalidToolCallError) as context:
            AgentRuntime(provider, tool_executor=executor).run(
                structured_price_input()
            )

        self.assertEqual(context.exception.code, "PROVIDER_INVALID_TOOL_CALL")
        self.assertEqual(context.exception.missing_fields, ("scenarios",))
        self.assertEqual(executor.calls, [])

    def test_omitted_argument_absent_from_state_becomes_missing_input(self):
        provider, _ = self.provider(
            ollama_tool_response(arguments={"menu_id": "M01"})
        )
        agent_input = AgentInput(
            message_id="ollama-user-missing",
            text="치킨마요 가격을 바꾸면 어때?",
        )

        with self.assertRaises(AgentMissingInputError) as context:
            AgentRuntime(provider).run(agent_input)

        self.assertEqual(context.exception.code, "MISSING_INPUT")
        self.assertEqual(context.exception.missing_input.fields, ("scenarios",))
        self.assertEqual(
            context.exception.agent_response.missing_input,
            context.exception.missing_input,
        )
        self.assertEqual(
            context.exception.missing_input.source_requirement,
            (ValueSource.USER,),
        )


if __name__ == "__main__":
    unittest.main()
