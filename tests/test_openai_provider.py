from copy import deepcopy
import json
import unittest

import requests

from src.agent_runtime import AgentRuntime
from src.agent_schemas import (
    AgentInput,
    DecisionAction,
    Message,
    MessageRole,
    Provenance,
    ToolCall,
    ValueSource,
)
from src.agent_tool_contracts import TOOL_SCHEMAS
from src.llm_provider import LLMProvider
from src.openai_provider import OpenAIProvider, OpenAIProviderError
from tests.test_agent_runtime import RecordingToolExecutor


class StubResponse:
    def __init__(self, payload, status_code=200):
        self.payload = deepcopy(payload)
        self.status_code = status_code

    def json(self):
        return deepcopy(self.payload)


class StubHttpClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, *, headers, json, timeout):
        self.requests.append(
            {
                "url": url,
                "headers": deepcopy(headers),
                "json": deepcopy(json),
                "timeout": timeout,
            }
        )
        return self.responses.pop(0)


def tool_response(arguments=None, name="compare_price_strategies"):
    arguments = arguments or {
        "menu_id": "M01",
        "scenarios": [
            {"name": "가격 인상", "list_price": 9500, "discount": 0}
        ],
    }
    return StubResponse(
        {
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "rs_1"},
                {
                    "type": "function_call",
                    "call_id": "call_openai_1",
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            ],
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens": 30,
                "total_tokens": 130,
            },
        }
    )


def final_response(decision="EXPERIMENT"):
    content = json.dumps(
        {
            "decision_claim": decision,
            "explanation": "합성 데이터 결과이며 근거 품질은 아직 미보정 상태입니다.",
            "next_action": "작은 범위의 검증을 먼저 준비해주세요.",
        },
        ensure_ascii=False,
    )
    return StubResponse(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                }
            ],
            "usage": {
                "input_tokens": 200,
                "input_tokens_details": {"cached_tokens": 50},
                "output_tokens": 40,
                "total_tokens": 240,
            },
        }
    )


def structured_input():
    inputs = {
        "menu_id": "M01",
        "scenarios": [
            {"name": "가격 인상", "list_price": 9500, "discount": 0}
        ],
    }
    return AgentInput(
        message_id="openai-user-1",
        text="치킨마요를 9,500원으로 올리면 어때?",
        business_inputs=inputs,
        provenance={
            name: Provenance(ValueSource.USER, f"openai-user-1:{name}")
            for name in inputs
        },
    )


class OpenAIProviderTests(unittest.TestCase):
    def provider(self, *responses):
        client = StubHttpClient(*responses)
        provider = OpenAIProvider(
            api_key="test-key",
            model="test-openai-model",
            http_client=client,
        )
        return provider, client

    def test_tool_call_response_converts_to_common_tool_call(self):
        provider, client = self.provider(tool_response())

        response = provider.generate(
            (Message(MessageRole.USER, "가격을 바꾸면 어때?"),),
            TOOL_SCHEMAS,
        )

        self.assertIsInstance(provider, LLMProvider)
        self.assertEqual(response.tool_calls[0].name, "compare_price_strategies")
        self.assertEqual(response.tool_calls[0].arguments["menu_id"], "M01")
        payload = client.requests[0]["json"]
        self.assertEqual(payload["model"], "test-openai-model")
        self.assertEqual(payload["tool_choice"], "required")
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertFalse(payload["store"])
        self.assertEqual(payload["tools"][1]["name"], "compare_price_strategies")
        self.assertIs(payload["tools"][1]["strict"], False)
        self.assertNotIn("text", payload)
        self.assertNotIn("test-key", json.dumps(payload))
        self.assertEqual(
            provider.usage_totals,
            {
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "output_tokens": 30,
                "total_tokens": 130,
            },
        )

    def test_final_response_uses_structured_outputs_and_common_response(self):
        provider, client = self.provider(tool_response(), final_response())
        provider.generate((Message(MessageRole.USER, "가격을 바꾸면 어때?"),), TOOL_SCHEMAS)
        messages = (
            Message(MessageRole.USER, "가격을 바꾸면 어때?"),
            Message(
                MessageRole.ASSISTANT,
                "",
                tool_calls=(
                    ToolCall(
                        "call_openai_1",
                        "compare_price_strategies",
                        {"menu_id": "M01", "scenarios": []},
                    ),
                ),
            ),
            Message(
                MessageRole.TOOL,
                '{"status":"ok"}',
                tool_call_id="call_openai_1",
                tool_name="compare_price_strategies",
            ),
        )

        response = provider.generate(messages, TOOL_SCHEMAS)

        self.assertEqual(response.decision_claim, DecisionAction.EXPERIMENT)
        self.assertIn("미보정", response.text)
        self.assertIn("검증", response.next_action)
        payload = client.requests[1]["json"]
        self.assertEqual(payload["tool_choice"], "none")
        response_format = payload["text"]["format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["strict"])
        self.assertNotIn(
            "minLength",
            response_format["schema"]["properties"]["explanation"],
        )
        self.assertEqual(payload["input"][1]["type"], "reasoning")
        self.assertEqual(payload["input"][2]["type"], "function_call")
        function_output = payload["input"][-1]
        self.assertEqual(function_output["type"], "function_call_output")
        self.assertEqual(function_output["call_id"], "call_openai_1")

    def test_unknown_tool_name_is_rejected(self):
        provider, _ = self.provider(tool_response(name="unknown_tool"))
        with self.assertRaises(OpenAIProviderError) as context:
            provider.generate((Message(MessageRole.USER, "질문"),), TOOL_SCHEMAS)
        self.assertEqual(context.exception.code, "PROVIDER_INVALID_TOOL_CALL")

    def test_invalid_tool_arguments_are_rejected(self):
        response = tool_response()
        response.payload["output"][1]["arguments"] = "not-json"
        provider, _ = self.provider(response)
        with self.assertRaises(OpenAIProviderError) as context:
            provider.generate((Message(MessageRole.USER, "질문"),), TOOL_SCHEMAS)
        self.assertEqual(context.exception.code, "PROVIDER_INVALID_TOOL_CALL")

    def test_provider_only_converts_and_runtime_executes_tool(self):
        provider, client = self.provider(tool_response(), final_response())
        executor = RecordingToolExecutor(
            {
                "status": "ok",
                "recommended_action": {"scenario_id": "scenario-1"},
                "strategies": [],
            }
        )

        result = AgentRuntime(provider, tool_executor=executor).run(structured_input())

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(result.tool_result.raw, executor.result)
        self.assertEqual(result.final_response.decision_claim, DecisionAction.EXPERIMENT)

    def test_http_errors_are_normalized_without_secret_or_message(self):
        provider, _ = self.provider(
            StubResponse(
                {
                    "error": {
                        "code": "invalid_api_key",
                        "type": "invalid_request_error",
                        "message": "secret-bearing provider message",
                    }
                },
                status_code=401,
            )
        )
        with self.assertRaises(OpenAIProviderError) as context:
            provider.generate((Message(MessageRole.USER, "질문"),), TOOL_SCHEMAS)
        self.assertEqual(context.exception.code, "OPENAI_AUTHENTICATION_ERROR")
        serialized = json.dumps(context.exception.details)
        self.assertNotIn("secret-bearing", serialized)
        self.assertNotIn("test-key", serialized)

    def test_malformed_final_response_is_normalized(self):
        malformed = StubResponse(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "not-json"}],
                    }
                ],
            }
        )
        provider, _ = self.provider(tool_response(), malformed)
        first = provider.generate(
            (Message(MessageRole.USER, "질문"),),
            TOOL_SCHEMAS,
        )
        messages = (
            Message(MessageRole.USER, "질문"),
            Message(
                MessageRole.ASSISTANT,
                "",
                tool_calls=first.tool_calls,
            ),
            Message(
                MessageRole.TOOL,
                '{"status":"ok"}',
                tool_call_id=first.tool_calls[0].call_id,
                tool_name=first.tool_calls[0].name,
            ),
        )

        with self.assertRaises(OpenAIProviderError) as context:
            provider.generate(messages, TOOL_SCHEMAS)

        self.assertEqual(context.exception.code, "OPENAI_RESPONSE_INVALID")

    def test_timeout_is_normalized(self):
        class TimeoutClient:
            def post(self, *args, **kwargs):
                raise requests.Timeout("secret provider detail")

        provider = OpenAIProvider(
            api_key="test-key",
            model="test-model",
            http_client=TimeoutClient(),
        )
        with self.assertRaises(OpenAIProviderError) as context:
            provider.generate((Message(MessageRole.USER, "질문"),), TOOL_SCHEMAS)

        self.assertEqual(context.exception.code, "OPENAI_TIMEOUT")
        self.assertNotIn("secret provider detail", json.dumps(context.exception.details))
    def test_unsupported_model_and_rate_limit_are_distinct(self):
        for status, provider_code, expected in (
            (404, "model_not_found", "OPENAI_UNSUPPORTED_MODEL"),
            (429, "rate_limit_exceeded", "OPENAI_RATE_LIMIT"),
        ):
            with self.subTest(status=status):
                provider, _ = self.provider(
                    StubResponse(
                        {"error": {"code": provider_code, "type": "invalid_request_error"}},
                        status_code=status,
                    )
                )
                with self.assertRaises(OpenAIProviderError) as context:
                    provider.generate(
                        (Message(MessageRole.USER, "질문"),),
                        TOOL_SCHEMAS,
                    )
                self.assertEqual(context.exception.code, expected)


if __name__ == "__main__":
    unittest.main()