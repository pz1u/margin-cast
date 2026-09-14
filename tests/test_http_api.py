import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from src.http_api import create_server, dispatch_api


class FakeService:
    def get_capabilities(self):
        return {"status": "ok", "supported_menus": [{"menu_id": "M01"}]}

    def compare_price_strategies(self, **arguments):
        return {"status": "ok", "request": arguments, "strategies": []}

    def simulate_bundle_strategy(self, **arguments):
        return {"status": "ok", "request": arguments, "strategy": {}}


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
