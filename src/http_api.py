"""MarginCast 계산 엔진과 정적 웹사이트를 같은 HTTP 서버로 제공한다."""

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from src.agent_tool_contracts import error_result, execute_tool
from src.decision_service import MarginCastDecisionService, SERVICE_VERSION


MAX_BODY_BYTES = 64 * 1024
API_ROUTES = {
    ("GET", "/api/capabilities"): "get_margincast_capabilities",
    ("POST", "/api/strategies/price"): "compare_price_strategies",
    ("POST", "/api/strategies/bundle"): "simulate_bundle_strategy",
}
ERROR_STATUS = {
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "PAYLOAD_TOO_LARGE": 413,
    "PANEL_NOT_FOUND": 503,
    "INVALID_PANEL": 503,
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


def dispatch_api(method, path, body=None, service=None):
    """HTTP 입력을 도구 계약으로 전달하고 상태 코드와 JSON 객체를 반환한다."""
    method = method.upper()
    if path == "/api/health":
        if method != "GET":
            payload = error_result("METHOD_NOT_ALLOWED", "GET 요청만 지원합니다.")
            return api_status(payload), payload
        return 200, {
            "status": "ok",
            "service": "MarginCast HTTP API",
            "version": SERVICE_VERSION,
        }

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


def _handler_class(service, static_dir):
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
            status, payload = dispatch_api(method, path, body, service)
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


def create_server(host="127.0.0.1", port=8000, *, service=None, static_dir=None):
    root = Path(__file__).resolve().parents[1]
    service = service or MarginCastDecisionService()
    static_dir = static_dir or root / "web"
    return ThreadingHTTPServer((host, port), _handler_class(service, static_dir))


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
