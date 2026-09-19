import os
from functools import partial
import json
from pathlib import Path
import tempfile
import unittest

from src.agent_result_router import AgentResultRouter
from src.agent_runtime import AgentRuntime
from src.agent_schemas import AgentInput, Provenance, ValueSource
from src.agent_tool_contracts import execute_tool
from src.decision_service import MarginCastDecisionService
from src.execution_defaults import get_execution_defaults
from src.generate_data import generate_dataset, save_dataset
from src.ollama_provider import OllamaProvider
from src.prepare_analysis_data import prepare_analysis_data
from src.response_policy import PriceResponsePolicy


RUN_OLLAMA_INTEGRATION = (
    os.getenv("RUN_OLLAMA_INTEGRATION") == "1"
    and bool(os.getenv("OLLAMA_BASE_URL"))
    and bool(os.getenv("OLLAMA_MODEL"))
)


@unittest.skipUnless(
    RUN_OLLAMA_INTEGRATION,
    "RUN_OLLAMA_INTEGRATION=1과 Ollama 환경변수가 있을 때만 실행합니다.",
)
class OllamaIntegrationTests(unittest.TestCase):
    def test_price_question_reaches_engine_and_response_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tables, truth = generate_dataset()
            save_dataset(tables, truth, root)
            panel_path = root / "processed" / "demand_panel.csv"
            prepare_analysis_data(root, panel_path)
            (root / "ground_truth.json").unlink()
            service = MarginCastDecisionService(panel_path)

            business_inputs = {
                "menu_id": "M01",
                "scenarios": [
                    {
                        "name": "가격 인상",
                        "list_price": 9500,
                        "discount": 0,
                    }
                ],
            }
            agent_input = AgentInput(
                message_id="ollama-integration-user",
                text="치킨마요를 9,500원으로 올리면 어때?",
                business_inputs=business_inputs,
                provenance={
                    name: Provenance(
                        ValueSource.USER,
                        f"ollama-integration-user:{name}",
                    )
                    for name in business_inputs
                },
            )
            runtime = AgentRuntime(
                OllamaProvider(timeout_seconds=600),
                tool_executor=partial(execute_tool, service=service),
            )

            run_result = runtime.run(
                agent_input,
                timing_label=os.getenv("OLLAMA_TIMING_LABEL", "unspecified"),
            )
            outcome = AgentResultRouter(
                PriceResponsePolicy(),
                execution_defaults=get_execution_defaults(),
            ).route(run_result)

            print(
                "\nOLLAMA_SMOKE_RESULT="
                + json.dumps(
                    {
                        "model": os.environ["OLLAMA_MODEL"],
                        "execution_id": run_result.execution_id,
                        "user_input": agent_input.text,
                        "tool_name": run_result.tool_call.name,
                        "tool_arguments": run_result.tool_call.arguments,
                        "tool_result_status": run_result.tool_result.raw["status"],
                        "decision_claim": run_result.final_response.decision_claim.value,
                        "policy_status": outcome.policy_validation["status"],
                        "policy_violations": outcome.policy_validation["violations"],
                        "explanation": run_result.final_response.text,
                        "next_action": run_result.final_response.next_action,
                        "timings_ms": outcome.timings_ms,
                    },
                    ensure_ascii=True,
                )
            )
            self.assertEqual(
                run_result.tool_call.name,
                "compare_price_strategies",
            )
            self.assertEqual(run_result.tool_result.raw["status"], "ok")
            self.assertEqual(outcome.policy_validation["status"], "PASS")
            self.assertIsNotNone(outcome.agent_response)
            self.assertEqual(
                outcome.agent_response.presentation.next_action,
                run_result.final_response.next_action,
            )


if __name__ == "__main__":
    unittest.main()
