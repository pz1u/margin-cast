import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.kakao_geocoding import (
    API_URL,
    KakaoConfigurationError,
    KakaoGeocodingError,
    geocode_address,
    get_rest_api_key,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload, status_code=200):
        self.response = FakeResponse(payload, status_code)
        self.call = None

    def get(self, url, headers, params, timeout):
        self.call = {
            "url": url,
            "headers": headers,
            "params": params,
            "timeout": timeout,
        }
        return self.response


def address_payload():
    return {
        "meta": {"total_count": 1},
        "documents": [
            {
                "address_name": "서울 중구 태평로1가 31",
                "road_address": {"address_name": "서울 중구 세종대로 110"},
                "x": "126.978652258309",
                "y": "37.566826004661",
            }
        ],
    }


class KakaoGeocodingTests(unittest.TestCase):
    def test_key_reads_dotenv_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("KAKAO_REST_API_KEY=file-key\n", encoding="utf-8")
            with patch.dict(os.environ, {"KAKAO_REST_API_KEY": "environment-key"}):
                self.assertEqual(get_rest_api_key(env_path=env_path), "environment-key")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(get_rest_api_key(env_path=env_path), "file-key")

    def test_missing_key_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(KakaoConfigurationError):
                    get_rest_api_key(env_path=Path(directory) / ".env")

    def test_address_is_normalized_to_wgs84_coordinates(self):
        session = FakeSession(address_payload())
        result = geocode_address(
            "서울특별시 중구 세종대로 110",
            rest_api_key="secret-key",
            session=session,
        )

        self.assertEqual(session.call["url"], API_URL)
        self.assertEqual(session.call["headers"]["Authorization"], "KakaoAK secret-key")
        self.assertEqual(session.call["params"]["size"], 1)
        self.assertEqual(result["address_name"], "서울 중구 세종대로 110")
        self.assertAlmostEqual(result["latitude"], 37.566826004661)
        self.assertAlmostEqual(result["longitude"], 126.978652258309)
        self.assertNotIn("secret-key", repr(result))

    def test_no_search_result_is_rejected(self):
        session = FakeSession({"meta": {"total_count": 0}, "documents": []})
        with self.assertRaisesRegex(KakaoGeocodingError, "검색 결과가 없습니다"):
            geocode_address("없는 주소", rest_api_key="key", session=session)

    def test_api_authorization_error_is_actionable(self):
        session = FakeSession(
            {"errorType": "NotAuthorizedError", "message": "service disabled"},
            status_code=403,
        )
        with self.assertRaisesRegex(KakaoGeocodingError, "NotAuthorizedError"):
            geocode_address("서울", rest_api_key="key", session=session)


if __name__ == "__main__":
    unittest.main()
