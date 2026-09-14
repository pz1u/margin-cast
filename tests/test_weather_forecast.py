import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.weather_forecast import (
    API_URL,
    KST,
    KmaApiError,
    KmaConfigurationError,
    fetch_village_forecast,
    get_service_key,
    latest_available_base,
    parse_forecast_response,
    precipitation_mm_estimate,
    resolve_forecast_location,
    resolve_provider,
)


def sample_payload(result_code="00"):
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": "NORMAL_SERVICE"},
            "body": {
                "items": {
                    "item": [
                        {"fcstDate": "20260914", "fcstTime": "1200", "category": "TMP", "fcstValue": "24", "nx": 60, "ny": 127},
                        {"fcstDate": "20260914", "fcstTime": "1200", "category": "PCP", "fcstValue": "1.0mm 미만", "nx": 60, "ny": 127},
                        {"fcstDate": "20260914", "fcstTime": "1200", "category": "PTY", "fcstValue": "1", "nx": 60, "ny": 127},
                        {"fcstDate": "20260914", "fcstTime": "1200", "category": "REH", "fcstValue": "78", "nx": 60, "ny": 127},
                        {"fcstDate": "20260914", "fcstTime": "1200", "category": "POP", "fcstValue": "60", "nx": 60, "ny": 127},
                        {"fcstDate": "20260914", "fcstTime": "1200", "category": "SKY", "fcstValue": "4", "nx": 60, "ny": 127},
                    ]
                }
            },
        }
    }


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return sample_payload()


class FakeSession:
    def __init__(self):
        self.call = None

    def get(self, url, params, timeout):
        self.call = {"url": url, "params": params, "timeout": timeout}
        return FakeResponse()


class WeatherForecastTests(unittest.TestCase):
    def test_service_key_reads_dotenv_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("KMA_SERVICE_KEY=file-key\n", encoding="utf-8")
            with patch.dict(os.environ, {"KMA_SERVICE_KEY": "environment-key"}):
                self.assertEqual(get_service_key(env_path=env_path), "environment-key")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(get_service_key(env_path=env_path), "file-key")

    def test_missing_service_key_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(KmaConfigurationError):
                    get_service_key(env_path=Path(directory) / ".env")

    def test_latest_base_uses_previous_day_before_first_release(self):
        now = datetime(2026, 9, 14, 2, 10, tzinfo=KST)
        self.assertEqual(
            latest_available_base(now),
            datetime(2026, 9, 13, 23, 0, tzinfo=KST),
        )

    def test_response_is_grouped_into_simulation_context(self):
        rows = parse_forecast_response(sample_payload())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tmp_c"], 24.0)
        self.assertEqual(rows[0]["reh_pct"], 78.0)
        self.assertEqual(rows[0]["pcp_mm_estimate"], 0.5)
        self.assertTrue(rows[0]["is_rain"])
        self.assertFalse({"TMP", "PCP", "PTY", "REH", "POP", "SKY"}.intersection(rows[0]))

    def test_api_error_code_is_rejected(self):
        with self.assertRaises(KmaApiError):
            parse_forecast_response(sample_payload("30"))

    def test_fetch_uses_auth_key_without_exposing_it_in_result(self):
        session = FakeSession()
        rows = fetch_village_forecast(
            60,
            127,
            service_key="secret-key",
            base_datetime=datetime(2026, 9, 14, 5, 0, tzinfo=KST),
            session=session,
            provider="data_go",
        )
        self.assertEqual(session.call["url"], API_URL)
        self.assertEqual(session.call["params"]["serviceKey"], "secret-key")
        self.assertEqual(session.call["params"]["base_time"], "0500")
        self.assertNotIn("secret-key", repr(rows))

    def test_provider_is_detected_without_exposing_or_double_encoding_key(self):
        self.assertEqual(resolve_provider("abc%2Bdef"), "data_go")
        self.assertEqual(resolve_provider("short-api-hub-key"), "api_hub")
        with self.assertRaises(KmaConfigurationError):
            resolve_provider("key", "unknown")

    def test_address_is_converted_to_forecast_grid(self):
        def fake_geocoder(address, env_path):
            self.assertEqual(address, "서울 중구 세종대로 110")
            self.assertEqual(env_path, Path("custom.env"))
            return {
                "address_name": address,
                "latitude": 37.5665,
                "longitude": 126.9780,
            }

        nx, ny, location = resolve_forecast_location(
            address="서울 중구 세종대로 110",
            env_path=Path("custom.env"),
            geocoder=fake_geocoder,
        )
        self.assertEqual((nx, ny), (60, 127))
        self.assertEqual(location["address_name"], "서울 중구 세종대로 110")

    def test_address_cannot_be_combined_with_coordinates(self):
        with self.assertRaises(KmaConfigurationError):
            resolve_forecast_location(address="서울", nx=60, ny=127)

    def test_precipitation_categories_are_numeric(self):
        self.assertEqual(precipitation_mm_estimate("강수없음"), 0.0)
        self.assertEqual(precipitation_mm_estimate("30.0~50.0mm"), 40.0)
        self.assertEqual(precipitation_mm_estimate("50.0mm 이상"), 50.0)
        self.assertIsNone(precipitation_mm_estimate("2"))

    def test_extended_period_precipitation_code_is_not_treated_as_mm(self):
        payload = sample_payload()
        items = payload["response"]["body"]["items"]["item"]
        pcp = next(item for item in items if item["category"] == "PCP")
        pcp["fcstValue"] = "2"
        pty = next(item for item in items if item["category"] == "PTY")
        pty["fcstValue"] = "0"
        row = parse_forecast_response(payload)[0]
        self.assertIsNone(row["pcp_mm_estimate"])
        self.assertEqual(row["pcp_category_code"], 2)
        self.assertTrue(row["is_rain"])


if __name__ == "__main__":
    unittest.main()
