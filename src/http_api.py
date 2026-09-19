"""MarginCast 계산 엔진과 정적 웹사이트를 같은 HTTP 서버로 제공한다."""

import argparse
from functools import partial
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from src.agent_audit import AgentAuditStore
from src.agent_chat_service import AgentChatRequestError, AgentChatService
from src.agent_tool_contracts import error_result, execute_tool
from src.decision_service import DecisionServiceError, MarginCastDecisionService, SERVICE_VERSION
from src.evidence_quality import (
    EVIDENCE_QUALITY_POLICY_FINGERPRINT,
    EVIDENCE_QUALITY_VERSION,
)
from src.experiment_feedback import ExperimentFeedbackError, ExperimentFeedbackStore
from src.forecast_decision_service import ForecastDecisionError, ForecastDecisionService
from src.ollama_provider import OllamaProvider


MAX_BODY_BYTES = 64 * 1024
API_ROUTES = {
    ("GET", "/api/capabilities"): "get_margincast_capabilities",
    ("POST", "/api/strategies/price"): "compare_price_strategies",
    ("POST", "/api/strategies/bundle"): "simulate_bundle_strategy",
}
FORECAST_PRICE_ROUTE = "/api/strategies/price/forecast"
FEEDBACK_ROUTE = "/api/experiments/feedback"
FEEDBACK_PLAN_ROUTE = "/api/experiments/plans"
FEEDBACK_SUMMARY_ROUTE = "/api/experiments/feedback/summary"
AGENT_CHAT_ROUTE = "/api/agent/chat"
ERROR_STATUS = {
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "PAYLOAD_TOO_LARGE": 413,
    "PANEL_NOT_FOUND": 503,
    "INVALID_PANEL": 503,
    "FORECAST_CONFIGURATION_ERROR": 503,
    "FORECAST_LOOKUP_FAILED": 502,
    "PERIOD_MISMATCH": 400,
    "FEEDBACK_STORE_CORRUPT": 500,
    "FEEDBACK_STORE_UNAVAILABLE": 503,
    "FEEDBACK_NOT_FOUND": 404,
    "FEEDBACK_ALREADY_COMPLETED": 409,
}


def _decode_json(body):
    if isinstance(body, dict):
        return body
    if body is None:
        return {}
    try:
        text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return error_result("INVALID_ARGUMENTS", "요청 본문이 올바른 JSON이 아닙니다.")
    if not isinstance(value, dict):
        return error_result("INVALID_ARGUMENTS", "요청 본문은 JSON 객체여야 합니다.")
    return value


def api_status(payload):
    if payload.get("status") != "error":
        return 200
    code = payload.get("error", {}).get("code", "")
    if code in ERROR_STATUS:
        return ERROR_STATUS[code]
    if code.startswith("INVALID_"):
        return 400
    return 422


def dispatch_api(
    method,
    path,
    body=None,
    service=None,
    forecast_service=None,
    feedback_store=None,
    agent_chat_service=None,
):
    """HTTP 입력을 도구 계약으로 전달하고 상태 코드와 JSON 객체를 반환한다."""
    method = method.upper()
    if path == AGENT_CHAT_ROUTE:
        if method != "POST":
            payload = error_result("METHOD_NOT_ALLOWED", "POST 요청만 지원합니다.")
            return api_status(payload), payload
        arguments = _decode_json(body)
        if isinstance(arguments, dict) and arguments.get("status") == "error":
            return api_status(arguments), arguments
        allowed = {"session_id", "message"}
        unknown = sorted(set(arguments) - allowed)
        if unknown or "message" not in arguments:
            payload = error_result(
                "INVALID_ARGUMENTS",
                "Agent 대화 요청의 필드를 확인하세요.",
                {
                    "unknown_fields": unknown,
                    "missing_fields": (
                        [] if "message" in arguments else ["message"]
                    ),
                },
            )
            return api_status(payload), payload
        if agent_chat_service is None:
            return 503, {
                "status": "ERROR",
                "execution_id": None,
                "recommendation_id": None,
                "error": {
                    "code": "AGENT_UNAVAILABLE",
                    "message": "Agent 서비스를 사용할 수 없습니다.",
                    "retryable": True,
                },
            }
        try:
            return agent_chat_service.chat(
                arguments.get("session_id"),
                arguments["message"],
            )
        except AgentChatRequestError as error:
            return 400, {
                "status": "ERROR",
                "execution_id": None,
                "recommendation_id": None,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": False,
                },
            }

    if path == "/api/health":
        if method != "GET":
            payload = error_result("METHOD_NOT_ALLOWED", "GET 요청만 지원합니다.")
            return api_status(payload), payload
        return 200, {
            "status": "ok",
            "service": "MarginCast HTTP API",
            "version": SERVICE_VERSION,
            "evidence_quality": {
                "version": EVIDENCE_QUALITY_VERSION,
                "formula_fingerprint": EVIDENCE_QUALITY_POLICY_FINGERPRINT,
            },
        }

    if path == FEEDBACK_ROUTE:
        if method != "POST":
            payload = error_result("METHOD_NOT_ALLOWED", "POST 요청만 지원합니다.")
            return api_status(payload), payload
        arguments = _decode_json(body)
        if isinstance(arguments, dict) and arguments.get("status") == "error":
            return api_status(arguments), arguments
        feedback_store = feedback_store or ExperimentFeedbackStore()
        try:
            allowed = {"feedback_id", "baseline_method", "actual"}
            unknown = sorted(set(arguments) - allowed)
            missing = sorted(allowed - set(arguments))
            if unknown or missing:
                raise ExperimentFeedbackError(
                    "INVALID_FEEDBACK",
                    "실제 결과 요청의 필드를 확인하세요.",
                    {"unknown_fields": unknown, "missing_fields": missing},
                )
            record = feedback_store.complete(**arguments)
            payload = {
                "status": "ok",
                "record": record,
                "summary": feedback_store.summary(),
            }
        except ExperimentFeedbackError as error:
            payload = error_result(
                error.code,
                error.message,
                error.details,
                retryable=error.code == "FEEDBACK_STORE_UNAVAILABLE",
            )
        return api_status(payload), payload

    if path == FEEDBACK_PLAN_ROUTE:
        feedback_store = feedback_store or ExperimentFeedbackStore()
        try:
            if method == "POST":
                arguments = _decode_json(body)
                if isinstance(arguments, dict) and arguments.get("status") == "error":
                    return api_status(arguments), arguments
                plan = feedback_store.plan(arguments)
                payload = {"status": "ok", "plan": plan}
            elif method == "GET":
                payload = {"status": "ok", "plans": feedback_store.pending()}
            else:
                payload = error_result(
                    "METHOD_NOT_ALLOWED", "GET 또는 POST 요청만 지원합니다."
                )
        except ExperimentFeedbackError as error:
            payload = error_result(
                error.code,
                error.message,
                error.details,
                retryable=error.code == "FEEDBACK_STORE_UNAVAILABLE",
            )
        return api_status(payload), payload

    if path == FEEDBACK_SUMMARY_ROUTE:
        if method != "GET":
            payload = error_result("METHOD_NOT_ALLOWED", "GET 요청만 지원합니다.")
            return api_status(payload), payload
        feedback_store = feedback_store or ExperimentFeedbackStore()
        try:
            payload = feedback_store.summary()
        except ExperimentFeedbackError as error:
            payload = error_result(error.code, error.message, error.details)
        return api_status(payload), payload

    if path == FORECAST_PRICE_ROUTE:
        if method != "POST":
            payload = error_result("METHOD_NOT_ALLOWED", "POST 요청만 지원합니다.")
            return api_status(payload), payload
        arguments = _decode_json(body)
        if isinstance(arguments, dict) and arguments.get("status") == "error":
            return api_status(arguments), arguments
        allowed = {
            "address",
            "menu_id",
            "scenarios",
            "horizon_days",
            "simulations",
            "seed",
        }
        unknown = sorted(set(arguments) - allowed)
        missing = sorted(allowed - set(arguments))
        if unknown or missing:
            payload = error_result(
                "INVALID_ARGUMENTS",
                "실제 예보 비교 요청의 필드를 확인하세요.",
                {"unknown_fields": unknown, "missing_fields": missing},
            )
            return api_status(payload), payload
        forecast_service = forecast_service or ForecastDecisionService(service)
        try:
            payload = forecast_service.compare_price_strategies(**arguments)
        except ForecastDecisionError as error:
            payload = error_result(
                error.code,
                error.message,
                error.details,
                retryable=error.retryable,
            )
        except DecisionServiceError as error:
            payload = error_result(
                error.code,
                error.message,
                error.details,
                retryable=error.code in {"PANEL_NOT_FOUND", "INVALID_PANEL"},
            )
        except (TypeError, ValueError) as error:
            payload = error_result("INVALID_ARGUMENTS", str(error))
        return api_status(payload), payload

    tool_name = API_ROUTES.get((method, path))
    if tool_name is None:
        methods = {route_method for route_method, route_path in API_ROUTES if route_path == path}
        if methods:
            payload = error_result(
                "METHOD_NOT_ALLOWED",
                f"{', '.join(sorted(methods))} 요청만 지원합니다.",
            )
        else:
            payload = error_result("NOT_FOUND", f"지원하지 않는 API 경로입니다: {path}")
        return api_status(payload), payload

    arguments = {} if method == "GET" else _decode_json(body)
    if isinstance(arguments, dict) and arguments.get("status") == "error":
        return api_status(arguments), arguments
    payload = execute_tool(tool_name, arguments, service)
    return api_status(payload), payload


def _handler_class(
    service,
    forecast_service,
    feedback_store,
    agent_chat_service,
    static_dir,
):
    static_dir = Path(static_dir).resolve()

    class MarginCastRequestHandler(BaseHTTPRequestHandler):
        server_version = "MarginCastHTTP/0.1"

        def _headers(self, status, content_type, length):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'",
            )
            self.end_headers()

        def _send_json(self, status, payload):
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(content))
            self.wfile.write(content)

        def _send_api(self, method, path, body=None):
            status, payload = dispatch_api(
                method,
                path,
                body,
                service,
                forecast_service,
                feedback_store,
                agent_chat_service,
            )
            self._send_json(status, payload)

        def _send_static(self, request_path):
            relative = "index.html" if request_path == "/" else request_path.lstrip("/")
            candidate = (static_dir / relative).resolve()
            if candidate != static_dir and static_dir not in candidate.parents:
                self._send_json(404, error_result("NOT_FOUND", "파일을 찾을 수 없습니다."))
                return
            if not candidate.is_file():
                self._send_json(404, error_result("NOT_FOUND", "파일을 찾을 수 없습니다."))
                return
            content = candidate.read_bytes()
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {
                "application/javascript",
                "application/json",
            }:
                content_type += "; charset=utf-8"
            self._headers(200, content_type, len(content))
            self.wfile.write(content)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                self._send_api("GET", path)
            else:
                self._send_static(path)

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            if content_length > MAX_BODY_BYTES:
                payload = error_result(
                    "PAYLOAD_TOO_LARGE",
                    f"요청 본문은 {MAX_BODY_BYTES:,}바이트 이하여야 합니다.",
                )
                self._send_json(api_status(payload), payload)
                return
            body = self.rfile.read(content_length)
            self._send_api("POST", path, body)

        def log_message(self, format, *args):
            return

    return MarginCastRequestHandler


def create_server(
    host="127.0.0.1",
    port=8000,
    *,
    service=None,
    static_dir=None,
    feedback_store=None,
    agent_chat_service=None,
):
    root = Path(__file__).resolve().parents[1]
    service = service or MarginCastDecisionService()
    forecast_service = ForecastDecisionService(service)
    feedback_store = feedback_store or ExperimentFeedbackStore()
    agent_chat_service = agent_chat_service or AgentChatService(
        provider_factory=OllamaProvider,
        tool_executor=partial(
            execute_tool,
            service=service,
            forecast_service=forecast_service,
            feedback_store=feedback_store,
        ),
        capabilities_loader=lambda: execute_tool(
            "get_margincast_capabilities",
            {},
            service=service,
        ),
        audit_store=AgentAuditStore(),
    )
    static_dir = static_dir or root / "web"
    return HTTPServer(
        (host, port),
        _handler_class(
            service,
            forecast_service,
            feedback_store,
            agent_chat_service,
            static_dir,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = create_server(args.host, args.port)
    print(f"MarginCast 웹 서버: http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
