import unittest

from src.agent_schemas import (
    AgentError,
    AgentErrorCategory,
    AgentErrorOrigin,
    AgentInput,
    AgentResponse,
    AgentResponseStatus,
    Decision,
    DecisionAction,
    Evidence,
    LLMResponse,
    Message,
    MessageRole,
    MissingInput,
    Provenance,
    StaticCapabilities,
    Strategy,
    ToolCall,
    ToolResult,
    ValueSource,
)


class AgentSchemaTests(unittest.TestCase):
    def test_static_capabilities_separate_user_inputs_and_defaults(self):
        capabilities = StaticCapabilities(
            tool_schemas=({"name": "compare_price_strategies"},),
            supported_features=("price_strategy",),
            output_schemas={},
            required_user_inputs={
                "compare_price_strategies": ("menu_id", "scenarios[].list_price")
            },
            execution_defaults={
                "compare_price_strategies": {
                    "horizon_days": 14,
                    "simulations": 10000,
                    "seed": 42,
                }
            },
        )

        defaults = capabilities.execution_defaults["compare_price_strategies"]
        self.assertEqual(defaults["simulations"], 10000)
        self.assertEqual(
            capabilities.required_user_inputs["compare_price_strategies"][0],
            "menu_id",
        )

    def test_agent_input_only_accepts_user_business_values(self):
        value = AgentInput(
            message_id="user-1",
            text="치킨마요를 9,500원으로 올리면 어때?",
            business_inputs={"list_price": 9500},
            provenance={
                "list_price": Provenance(ValueSource.USER, "user-1:list_price")
            },
        )
        self.assertEqual(value.business_inputs["list_price"], 9500)

        with self.assertRaises(ValueError):
            AgentInput(
                message_id="user-1",
                text="가격을 추천해줘",
                business_inputs={"list_price": 9500},
                provenance={
                    "list_price": Provenance(ValueSource.LLM, "assistant-1:list_price")
                },
            )

    def test_confirmed_strategy_rejects_llm_business_input(self):
        with self.assertRaises(ValueError):
            Strategy(
                name="LLM 가격 후보",
                operation="compare_price_strategies",
                business_inputs={"list_price": 9500},
                provenance={
                    "list_price": Provenance(ValueSource.LLM, "assistant-1:list_price")
                },
                confirmed=True,
            )

    def test_llm_response_normalizes_tool_calls(self):
        call = ToolCall(
            call_id="call-1",
            name="get_margincast_capabilities",
            arguments={},
        )
        response = LLMResponse(tool_calls=[call])
        self.assertEqual(response.tool_calls, (call,))

        history_message = Message(
            role=MessageRole.ASSISTANT,
            content="",
            tool_calls=response.tool_calls,
        )
        self.assertEqual(history_message.tool_calls, (call,))

    def test_completed_response_keeps_engine_facts_and_decision(self):
        decision = Decision(
            action=DecisionAction.EXPERIMENT,
            reason="근거 신뢰도가 본 실행 기준에 못 미친다.",
            source_ref="call-1:recommended_action",
        )
        evidence = Evidence(
            items={"price_events": 3},
            provenance={
                "price_events": Provenance(
                    ValueSource.ENGINE,
                    "call-1:strategies[1].confidence.evidence.price_events",
                )
            },
        )
        response = AgentResponse(
            status=AgentResponseStatus.COMPLETED,
            facts={"expected_profit_delta": 135000},
            fact_provenance={
                "expected_profit_delta": Provenance(
                    ValueSource.ENGINE,
                    "call-1:strategies[1].profit_delta.mean",
                )
            },
            decision=decision,
            evidence=evidence,
            explanation="이익 개선 가능성은 있지만 먼저 작은 실험이 필요합니다.",
        )
        self.assertEqual(response.decision.source, ValueSource.ENGINE)
        self.assertEqual(
            response.fact_provenance["expected_profit_delta"].source,
            ValueSource.ENGINE,
        )

        with self.assertRaises(ValueError):
            AgentResponse(
                status=AgentResponseStatus.COMPLETED,
                facts={"expected_profit_delta": 135000},
                fact_provenance={
                    "expected_profit_delta": Provenance(ValueSource.LLM, "assistant-1")
                },
            )

    def test_missing_input_response_requires_missing_input(self):
        with self.assertRaises(ValueError):
            AgentResponse(status=AgentResponseStatus.NEEDS_INPUT)

        missing = MissingInput(
            fields=("bundle_price",),
            reason="세트 전략 계산에 판매가가 필요합니다.",
            question="생각하신 세트 판매가는 얼마인가요?",
        )
        response = AgentResponse(
            status=AgentResponseStatus.NEEDS_INPUT,
            missing_input=missing,
        )
        self.assertEqual(response.missing_input.fields, ("bundle_price",))

    def test_error_response_rejects_invented_facts(self):
        tool_result = ToolResult(
            call_id="call-2",
            tool_name="compare_price_strategies",
            raw={"status": "error", "error": {"code": "UNSUPPORTED_MENU"}},
        )
        agent_error = AgentError(
            code=AgentErrorCategory.UNSUPPORTED,
            message="지원하지 않는 메뉴입니다.",
            origin=AgentErrorOrigin.TOOL,
        )
        with self.assertRaises(ValueError):
            AgentResponse(
                status=AgentResponseStatus.ERROR,
                facts={"expected_profit_delta": 135000},
                fact_provenance={
                    "expected_profit_delta": Provenance(
                        ValueSource.ENGINE,
                        "call-2:profit_delta.mean",
                    )
                },
                error=agent_error,
                tool_results=(tool_result,),
            )

    def test_agent_error_does_not_require_fake_tool_result(self):
        response = AgentResponse(
            status=AgentResponseStatus.ERROR,
            error=AgentError(
                code=AgentErrorCategory.ENGINE_ERROR,
                message="Ollama에 연결할 수 없습니다.",
                origin=AgentErrorOrigin.PROVIDER,
                retryable=True,
            ),
        )

        self.assertEqual(response.error.origin, AgentErrorOrigin.PROVIDER)
        self.assertEqual(response.tool_results, ())


if __name__ == "__main__":
    unittest.main()
