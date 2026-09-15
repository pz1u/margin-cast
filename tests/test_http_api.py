import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from src.decision_service import DecisionServiceError
from src.experiment_feedback import ExperimentFeedbackError
from src.forecast_decision_service import ForecastDecisionError
from src.http_api import create_server, dispatch_api


class FakeService:
    def get_capabilities(self):
        return {"status": "ok", "supported_menus": [{"menu_id": "M01"}]}

    def compare_price_strategies(self, **arguments):
        return {"status": "ok", "request": arguments, "strategies": []}

    def simulate_bundle_strategy(self, **arguments):
        return {"status": "ok", "request": arguments, "strategy": {}}


class FakeForecastService:
    def compare_price_strategies(self, **arguments):
        return {
            "status": "ok",
            "request": arguments,
            "weather_context_source": "kma_forecast",
            "strategies": [],
        }


class FakeFeedbackStore:
    def __init__(self):
        self.last_record = None

    def plan(self, arguments):
        self.last_record = arguments
        return {"feedback_id": "feedback-1", "menu_id": arguments["menu_id"], **arguments}

    def pending(self):
        return [] if self.last_record is None else [{"feedback_id": "feedback-1"}]

    def complete(self, feedback_id, baseline_method, actual):
        return {
            "feedback_id": feedback_id,
            "menu_id": "M01",
            "baseline_method": baseline_method,
            "actual": actual,
        }

    def summary(self, menu_id=None):
        return {"status": "ok", "menu_id": menu_id, "record_count": int(self.last_record is not None)}


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()

    def test_health_and_capabilities_routes(self):
        health_status, health = dispatch_api("GET", "/api/health", service=self.service)
        capability_status, capabilities = dispatch_api(
            "GET", "/api/capabilities", service=self.service
        )
        self.assertEqual(health_status, 200)
        self.assertEqual(health["service"], "MarginCast HTTP API")
        self.assertEqual(capability_status, 200)
        self.assertEqual(capabilities["supported_menus"][0]["menu_id"], "M01")

    def test_price_request_uses_existing_tool_contract(self):
        request = {
            "menu_id": "M01",
            "scenarios": [{"name": "인상", "list_price": 9500, "discount": 0}],
            "horizon_days": 14,
            "simulations": 500,
            "seed": 42,
        }
        status, payload = dispatch_api(
            "POST",
            "/api/strategies/price",
            json.dumps(request, ensure_ascii=False).encode("utf-8"),
            self.service,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["request"], request)

    def test_invalid_json_and_routes_have_http_errors(self):
        invalid_status, invalid = dispatch_api(
            "POST", "/api/strategies/price", b"{", self.service
        )
        missing_status, missing = dispatch_api("GET", "/api/unknown", service=self.service)
        method_status, method = dispatch_api(
            "GET", "/api/strategies/price", service=self.service
        )
        self.assertEqual((invalid_status, invalid["error"]["code"]), (400, "INVALID_ARGUMENTS"))
        self.assertEqual((missing_status, missing["error"]["code"]), (404, "NOT_FOUND"))
        self.assertEqual((method_status, method["error"]["code"]), (405, "METHOD_NOT_ALLOWED"))

    def test_forecast_price_route_keeps_address_out_of_agent_contract(self):
        request = {
            "address": "서울 중구 세종대로 110",
            "menu_id": "M01",
            "scenarios": [{"name": "인상", "list_price": 9500, "discount": 0}],
            "horizon_days": 4,
            "simulations": 500,
            "seed": 42,
        }
        status, payload = dispatch_api(
            "POST",
            "/api/strategies/price/forecast",
            request,
            self.service,
            FakeForecastService(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["weather_context_source"], "kma_forecast")
        self.assertEqual(payload["request"]["address"], request["address"])

    def test_forecast_route_maps_weather_and_domain_errors(self):
        request = {
            "address": "서울 중구 세종대로 110",
            "menu_id": "M01",
            "scenarios": [{"name": "인상", "list_price": 9500, "discount": 0}],
            "horizon_days": 4,
            "simulations": 500,
            "seed": 42,
        }

        class WeatherFailure:
            def compare_price_strategies(self, **arguments):
                raise ForecastDecisionError(
                    "FORECAST_LOOKUP_FAILED", "일시적인 조회 실패", retryable=True
                )

        class DomainFailure:
            def compare_price_strategies(self, **arguments):
                raise DecisionServiceError("UNSUPPORTED_MENU", "지원하지 않는 메뉴")

        weather_status, weather = dispatch_api(
            "POST", "/api/strategies/price/forecast", request,
            self.service, WeatherFailure()
        )
        domain_status, domain = dispatch_api(
            "POST", "/api/strategies/price/forecast", request,
            self.service, DomainFailure()
        )

        self.assertEqual(weather_status, 502)
        self.assertTrue(weather["error"]["retryable"])
        self.assertEqual((domain_status, domain["error"]["code"]), (422, "UNSUPPORTED_MENU"))

    def test_feedback_routes_preserve_plan_then_record_actual_result(self):
        store = FakeFeedbackStore()
        plan_request = {"menu_id": "M01", "experiment_name": "4일 가격 실험"}

        plan_status, planned = dispatch_api(
            "POST",
            "/api/experiments/plans",
            plan_request,
            self.service,
            FakeForecastService(),
            store,
        )
        pending_status, pending = dispatch_api(
            "GET",
            "/api/experiments/plans",
            service=self.service,
            feedback_store=store,
        )
        record_status, recorded = dispatch_api(
            "POST",
            "/api/experiments/feedback",
            {
                "feedback_id": "feedback-1",
                "baseline_method": "matched_period",
                "actual": {
                    "units": 97,
                    "contribution_profit": 610_000,
                    "baseline_contribution_profit": 570_000,
                },
            },
            self.service,
            FakeForecastService(),
            store,
        )
        summary_status, summary = dispatch_api(
            "GET",
            "/api/experiments/feedback/summary",
            service=self.service,
            feedback_store=store,
        )

        self.assertEqual(plan_status, 200)
        self.assertEqual(planned["plan"]["feedback_id"], "feedback-1")
        self.assertEqual(pending_status, 200)
        self.assertEqual(pending["plans"][0]["feedback_id"], "feedback-1")
        self.assertEqual(record_status, 200)
        self.assertEqual(recorded["record"]["feedback_id"], "feedback-1")
        self.assertIsNone(recorded["summary"]["menu_id"])
        self.assertEqual(summary_status, 200)
        self.assertEqual(summary["record_count"], 1)

    def test_feedback_route_maps_validation_error(self):
        class InvalidFeedbackStore(FakeFeedbackStore):
            def complete(self, feedback_id, baseline_method, actual):
                raise ExperimentFeedbackError(
                    "PERIOD_MISMATCH",
                    "기간 불일치",
                    {"observed_days": 5, "horizon_days": 4},
                )

        status, payload = dispatch_api(
            "POST",
            "/api/experiments/feedback",
            {
                "feedback_id": "feedback-1",
                "baseline_method": "matched_period",
                "actual": {},
            },
            self.service,
            FakeForecastService(),
            InvalidFeedbackStore(),
        )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "PERIOD_MISMATCH")

    def test_server_serves_static_site_and_json_api(self):
        with tempfile.TemporaryDirectory() as directory:
            static_dir = Path(directory)
            (static_dir / "index.html").write_text("<h1>MarginCast</h1>", encoding="utf-8")
            server = create_server("127.0.0.1", 0, service=self.service, static_dir=static_dir)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/")
                page = connection.getresponse()
                page_body = page.read().decode("utf-8")
                connection.request("GET", "/api/health")
                health = connection.getresponse()
                health_body = json.loads(health.read().decode("utf-8"))
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(page.status, 200)
        self.assertIn("MarginCast", page_body)
        self.assertEqual(health.status, 200)
        self.assertEqual(health_body["status"], "ok")


if __name__ == "__main__":
    unittest.main()
