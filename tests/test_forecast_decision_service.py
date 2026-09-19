import unittest

from src.forecast_decision_service import ForecastDecisionError, ForecastDecisionService
from src.kakao_geocoding import KakaoGeocodingError


class FakeDecisionService:
    def __init__(self):
        self.arguments = None

    def compare_price_strategies(self, **arguments):
        self.arguments = arguments
        return {
            "status": "ok",
            "request": {"horizon_days": arguments["horizon_days"]},
            "weather_context_source": "kma_forecast",
            "strategies": [],
        }


def fake_geocoder(address, env_path):
    return {
        "address_name": "서울 중구 세종대로 110",
        "latitude": 37.5665,
        "longitude": 126.9780,
    }


def fake_forecast_fetcher(nx, ny, env_path):
    return [
        {
            "date": f"2026-09-{15 + day:02d}",
            "time": "12:00",
            "forecast_at": f"2026-09-{15 + day:02d}T12:00:00+09:00",
            "is_rain": False,
        }
        for day in range(5)
    ]


class ForecastDecisionServiceTests(unittest.TestCase):
    def test_address_forecast_is_passed_to_decision_engine(self):
        decision_service = FakeDecisionService()
        service = ForecastDecisionService(
            decision_service,
            geocoder=fake_geocoder,
            forecast_fetcher=fake_forecast_fetcher,
        )
        result = service.compare_price_strategies(
            location={"address": "서울특별시 중구 세종대로 110"},
            menu_id="M01",
            scenarios=[{"name": "인상", "list_price": 9500, "discount": 0}],
            horizon_days=4,
            simulations=500,
            seed=42,
        )

        self.assertEqual(decision_service.arguments["forecasts"][0]["date"], "2026-09-15")
        self.assertEqual(result["weather"]["nx"], 60)
        self.assertEqual(result["weather"]["ny"], 127)
        self.assertEqual(result["weather"]["forecast_rows"], 5)
        self.assertEqual(result["weather"]["available_to"], "2026-09-19")
        self.assertEqual(result["weather"]["applied_to"], "2026-09-18")
        self.assertEqual(result["weather"]["location_source"], "address")
        self.assertNotIn("query", result["weather"])
        self.assertNotIn("address_name", result["weather"])
        self.assertNotIn("latitude", result["weather"])
        self.assertNotIn("longitude", result["weather"])

    def test_wgs84_and_kma_grid_locations_skip_address_lookup(self):
        def unexpected_geocoder(address, env_path):
            self.fail("좌표·격자 입력에서는 주소 검색을 호출하면 안 됩니다.")

        service = ForecastDecisionService(
            FakeDecisionService(),
            geocoder=unexpected_geocoder,
            forecast_fetcher=fake_forecast_fetcher,
        )
        locations = (
            ({"latitude": 37.5665, "longitude": 126.9780}, "wgs84"),
            ({"kma_nx": 60, "kma_ny": 127}, "kma_grid"),
        )
        for location, expected_source in locations:
            with self.subTest(location=location):
                result = service.compare_price_strategies(
                    location=location,
                    menu_id="M01",
                    scenarios=[],
                )
                self.assertEqual(result["weather"]["location_source"], expected_source)
                self.assertEqual((result["weather"]["nx"], result["weather"]["ny"]), (60, 127))

    def test_address_lookup_error_does_not_expose_address_query(self):
        address_query = "서울특별시 중구 테스트용 민감 주소"

        def failing_geocoder(address, env_path):
            raise KakaoGeocodingError(f"검색 실패: {address}")

        service = ForecastDecisionService(
            FakeDecisionService(),
            geocoder=failing_geocoder,
            forecast_fetcher=fake_forecast_fetcher,
        )
        with self.assertRaises(ForecastDecisionError) as context:
            service.compare_price_strategies(
                location={"address": address_query},
                menu_id="M01",
                scenarios=[],
            )

        self.assertEqual(context.exception.code, "FORECAST_LOOKUP_FAILED")
        self.assertNotIn(address_query, context.exception.message)
        self.assertNotIn(address_query, str(context.exception.details))

    def test_location_requires_exactly_one_complete_form(self):
        service = ForecastDecisionService(
            FakeDecisionService(),
            geocoder=fake_geocoder,
            forecast_fetcher=fake_forecast_fetcher,
        )
        invalid_locations = (
            {},
            {"latitude": 37.5665},
            {"kma_nx": 60},
            {"address": "서울", "kma_nx": 60, "kma_ny": 127},
        )
        for location in invalid_locations:
            with self.subTest(location=location), self.assertRaises(ForecastDecisionError) as context:
                service.compare_price_strategies(
                    location=location,
                    menu_id="M01",
                    scenarios=[],
                )
            self.assertEqual(context.exception.code, "INVALID_LOCATION")

    def test_forecast_horizon_is_limited_to_short_term_coverage(self):
        service = ForecastDecisionService(
            FakeDecisionService(),
            geocoder=fake_geocoder,
            forecast_fetcher=fake_forecast_fetcher,
        )
        with self.assertRaises(ForecastDecisionError) as context:
            service.compare_price_strategies(
                location={"address": "서울"},
                menu_id="M01",
                scenarios=[],
                horizon_days=7,
            )
        self.assertEqual(context.exception.code, "INVALID_HORIZON")

    def test_missing_forecast_days_returns_explicit_error(self):
        def one_day_forecast(nx, ny, env_path):
            return fake_forecast_fetcher(nx, ny, env_path)[:1]

        service = ForecastDecisionService(
            FakeDecisionService(),
            geocoder=fake_geocoder,
            forecast_fetcher=one_day_forecast,
        )
        with self.assertRaises(ForecastDecisionError) as context:
            service.compare_price_strategies(
                location={"address": "서울"},
                menu_id="M01",
                scenarios=[],
                horizon_days=4,
            )
        self.assertEqual(context.exception.code, "INSUFFICIENT_FORECAST")
        self.assertEqual(context.exception.details["available_days"], 1)
        self.assertTrue(context.exception.retryable)


if __name__ == "__main__":
    unittest.main()
