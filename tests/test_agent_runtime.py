import unittest

from src.agent_runtime import MarginCastAgentLoop
from src.agent_schemas import (
    AgentErrorCategory,
    AgentInput,
    AgentResponseStatus,
    DecisionAction,
    LLMResponse,
    MissingInput,
    Provenance,
    StaticCapabilities,
    ToolCall,
    ValueSource,
)


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, messages, tools):
        self.requests.append((tuple(messages), tuple(tools)))
        return self.responses.pop(0)


class RecordingToolExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def static_capabilities():
    return StaticCapabilities(
        tool_schemas=(
            {"name": "compare_price_strategies"},
            {"name": "simulate_bundle_strategy"},
        ),
        supported_features=("price_strategy", "bundle_strategy"),
        required_user_inputs={
            "compare_price_strategies": ("menu_id", "scenarios[].list_price"),
            "simulate_bundle_strategy": (
                "scenario.bundle_price",
                "scenario.take_rate",
                "scenario.copurchase_take_rate",
                "scenario.incremental_demand_rate",
                "scenario.cannibalization_rate",
            ),
        },
        execution_defaults={
            "compare_price_strategies": {
                "horizon_days": 14,
                "simulations": 10000,
                "seed": 42,
            },
            "simulate_bundle_strategy": {
                "horizon_days": 14,
                "simulations": 10000,
                "seed": 42,
            },
        },
    )


class AgentRuntimeTests(unittest.TestCase):
    def test_normal_request_calls_tool_and_preserves_engine_decision(self):
        tool_call = ToolCall(
            call_id="call-1",
            name="compare_price_strategies",
            arguments={
                "menu_id": "M01",
                "scenarios": [
                    {"name": "9500원 정가", "list_price": 9500, "discount": 0}
                ],
            },
            argument_provenance={
                "menu_id": Provenance(ValueSource.ENGINE, "capabilities:M01"),
                "scenarios[0].list_price": Provenance(ValueSource.USER, "user-1:list_price"),
                "scenarios[0].discount": Provenance(ValueSource.USER, "user-1:no_discount"),
            },
        )
        provider = ScriptedProvider(
            [
                LLMResponse(tool_calls=(tool_call,)),
                LLMResponse(text="계산 결과는 소규모 실험 단계입니다."),
            ]
        )
        engine_result = {
            "status": "ok",
            "recommended_action": {
                "name": "9500원 정가",
                "action": "EXPERIMENT",
                "reason": "근거 신뢰도가 본 실행 기준에 못 미친다.",
            },
            "strategies": [
                {
                    "name": "9500원 정가",
                    "profit_delta": {"mean": 135000},
                    "success_probability": 0.82,
                    "confidence": {"label": "MEDIUM"},
                }
            ],
        }
        executor = RecordingToolExecutor(engine_result)
        loop = MarginCastAgentLoop(provider, static_capabilities(), executor)

        response = loop.run(
            AgentInput(
                message_id="user-1",
                text="치킨마요를 9,500원으로 올리면 어때?",
                business_inputs={"list_price": 9500},
                provenance={
                    "list_price": Provenance(ValueSource.USER, "user-1:list_price")
                },
            )
        )

        self.assertEqual(response.status, AgentResponseStatus.COMPLETED)
        self.assertEqual(response.decision.action, DecisionAction.EXPERIMENT)
        self.assertEqual(response.tool_results[0].raw, engine_result)
        self.assertEqual(executor.calls[0][1]["simulations"], 10000)
        self.assertEqual(len(provider.requests), 2)
        tool_message = provider.requests[1][0][-1]
        self.assertIn('"profit_delta": {"mean": 135000}', tool_message.content)

    def test_missing_bundle_input_returns_question_without_tool_call(self):
        missing = MissingInput(
            fields=("scenario.bundle_price",),
            reason="세트 전략 계산에 판매가가 필요합니다.",
            question="생각하신 세트 판매가는 얼마인가요?",
        )
        provider = ScriptedProvider([LLMResponse(missing_input=missing)])
        executor = RecordingToolExecutor({"status": "ok"})
        loop = MarginCastAgentLoop(provider, static_capabilities(), executor)

        response = loop.run(
            AgentInput(message_id="user-2", text="치킨마요 콜라 세트 어때?")
        )

        self.assertEqual(response.status, AgentResponseStatus.NEEDS_INPUT)
        self.assertEqual(response.missing_input.fields, ("scenario.bundle_price",))
        self.assertEqual(response.missing_input.question, "생각하신 세트 판매가는 얼마인가요?")
        self.assertEqual(executor.calls, [])

        tool_call = ToolCall(
            call_id="call-missing",
            name="simulate_bundle_strategy",
            arguments={
                "scenario": {
                    "bundle_price": 10000,
                    "take_rate": 0.3,
                    "copurchase_take_rate": 0.5,
                    "incremental_demand_rate": 0.1,
                    "cannibalization_rate": 0.02,
                }
            },
        )
        provider = ScriptedProvider([LLMResponse(tool_calls=(tool_call,))])
        executor = RecordingToolExecutor(
            {
                "status": "error",
                "error": {
                    "code": "MISSING_INPUT",
                    "message": "가정의 근거가 필요합니다.",
                    "retryable": False,
                    "details": {"missing_fields": ["scenario.assumption_basis"]},
                },
            }
        )
        response = MarginCastAgentLoop(
            provider,
            static_capabilities(),
            executor,
        ).run(AgentInput(message_id="user-2b", text="세트 가격을 계산해줘"))

        self.assertEqual(response.status, AgentResponseStatus.NEEDS_INPUT)
        self.assertEqual(response.missing_input.fields, ("scenario.assumption_basis",))
        self.assertEqual(response.tool_results[0].raw["error"]["code"], "MISSING_INPUT")

    def test_insufficient_data_returns_no_numbers_or_invented_hold(self):
        call = ToolCall(
            call_id="call-3",
            name="compare_price_strategies",
            arguments={
                "menu_id": "M02",
                "scenarios": [{"name": "가격 변경", "list_price": 8500, "discount": 0}],
            },
        )
        provider = ScriptedProvider([LLMResponse(tool_calls=(call,))])
        executor = RecordingToolExecutor(
            {
                "status": "error",
                "error": {
                    "code": "INSUFFICIENT_DATA",
                    "message": "가격 변화 관측이 부족합니다.",
                    "retryable": False,
                },
            }
        )
        loop = MarginCastAgentLoop(provider, static_capabilities(), executor)

        response = loop.run(AgentInput(message_id="user-3", text="M02 가격을 분석해줘"))

        self.assertEqual(response.status, AgentResponseStatus.ERROR)
        self.assertEqual(response.error.code, AgentErrorCategory.INSUFFICIENT_DATA)
        self.assertEqual(response.facts, {})
        self.assertIsNone(response.decision)
        self.assertEqual(response.tool_results[0].raw["error"]["code"], "INSUFFICIENT_DATA")

    def test_tool_error_maps_to_agent_error_without_numbers(self):
        call = ToolCall(
            call_id="call-4",
            name="compare_price_strategies",
            arguments={
                "menu_id": "M01",
                "scenarios": [{"name": "잘못된 가격", "list_price": -1, "discount": 0}],
            },
        )
        provider = ScriptedProvider([LLMResponse(tool_calls=(call,))])
        executor = RecordingToolExecutor(
            {
                "status": "error",
                "error": {
                    "code": "INVALID_SCENARIO",
                    "message": "정가는 1,000원 이상이어야 합니다.",
                    "retryable": False,
                },
            }
        )
        loop = MarginCastAgentLoop(provider, static_capabilities(), executor)

        response = loop.run(AgentInput(message_id="user-4", text="가격을 바꿔줘"))

        self.assertEqual(response.status, AgentResponseStatus.ERROR)
        self.assertEqual(response.error.code, AgentErrorCategory.INVALID_INPUT)
        self.assertEqual(response.error.origin.value, "tool")
        self.assertEqual(response.facts, {})
        self.assertIsNone(response.decision)
        self.assertEqual(response.tool_results[0].raw["error"]["code"], "INVALID_SCENARIO")


if __name__ == "__main__":
    unittest.main()
