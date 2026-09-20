import os
import unittest
from unittest.mock import patch

from src.http_api import _environment_port, dispatch_api


class DeploymentConfigTests(unittest.TestCase):
    def test_health_alias_returns_same_safe_payload(self):
        api_status, api_payload = dispatch_api("GET", "/api/health")
        root_status, root_payload = dispatch_api("GET", "/health")

        self.assertEqual((api_status, root_status), (200, 200))
        self.assertEqual(api_payload, root_payload)
        serialized = str(root_payload).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("system prompt", serialized)

    def test_port_comes_from_environment(self):
        with patch.dict(os.environ, {"PORT": "9123"}, clear=False):
            self.assertEqual(_environment_port(), 9123)

    def test_invalid_environment_port_is_rejected(self):
        for value in ("not-a-number", "0", "65536"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"PORT": value}, clear=False):
                    with self.assertRaises(ValueError):
                        _environment_port()


if __name__ == "__main__":
    unittest.main()