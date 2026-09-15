"""매장 주소의 실제 단기예보를 가격 의사결정 계산에 연결한다."""

from datetime import datetime
from pathlib import Path

from src.decision_service import MarginCastDecisionService
from src.kakao_geocoding import (
    KakaoConfigurationError,
    KakaoGeocodingError,
    geocode_address,
)
from src.location_grid import KmaGridError, latlon_to_grid
from src.weather_forecast import KmaApiError, KmaConfigurationError, fetch_village_forecast


class ForecastDecisionError(RuntimeError):
    def __init__(self, code, message, details=None, *, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {} if details is None else details
        self.retryable = retryable


class ForecastDecisionService:
    def __init__(
        self,
        decision_service=None,
        *,
        env_path=None,
        geocoder=geocode_address,
        forecast_fetcher=fetch_village_forecast,
    ):
        root = Path(__file__).resolve().parents[1]
        self.decision_service = decision_service or MarginCastDecisionService()
        self.env_path = Path(env_path or root / ".env")
        self.geocoder = geocoder
        self.forecast_fetcher = forecast_fetcher

    def compare_price_strategies(
        self,
        *,
        address,
        menu_id,
        scenarios,
        horizon_days=4,
        simulations=10_000,
        seed=42,
    ):
        address = str(address or "").strip()
        if not 1 <= len(address) <= 200:
            raise ForecastDecisionError(
                "INVALID_ADDRESS", "매장 주소는 1~200자로 입력하세요."
            )
        if isinstance(horizon_days, bool) or not isinstance(horizon_days, int):
            raise ForecastDecisionError(
                "INVALID_HORIZON", "실제 예보 비교 기간은 정수여야 합니다."
            )
        if not 1 <= horizon_days <= 5:
            raise ForecastDecisionError(
                "INVALID_HORIZON",
                "기상청 단기예보를 사용하는 비교 기간은 1~5일이어야 합니다.",
            )

        try:
            location = self.geocoder(address, env_path=self.env_path)
            nx, ny = latlon_to_grid(location["latitude"], location["longitude"])
            forecasts = self.forecast_fetcher(nx, ny, env_path=self.env_path)
        except (KakaoConfigurationError, KmaConfigurationError) as error:
            raise ForecastDecisionError(
                "FORECAST_CONFIGURATION_ERROR", str(error)
            ) from error
        except (KakaoGeocodingError, KmaApiError, KmaGridError) as error:
            raise ForecastDecisionError(
                "FORECAST_LOOKUP_FAILED", str(error), retryable=True
            ) from error

        available_dates = sorted({row.get("date") for row in forecasts if row.get("date")})
        if len(available_dates) < horizon_days:
            raise ForecastDecisionError(
                "INSUFFICIENT_FORECAST",
                f"현재 예보는 {len(available_dates)}일만 사용할 수 있습니다.",
                {"available_days": len(available_dates), "requested_days": horizon_days},
                retryable=True,
            )

        result = self.decision_service.compare_price_strategies(
            menu_id=menu_id,
            scenarios=scenarios,
            horizon_days=horizon_days,
            simulations=simulations,
            seed=seed,
            forecasts=forecasts,
        )
        result["weather"] = {
            "source": "kma_short_term_forecast",
            "location_source": "kakao_address",
            "address_name": location.get("address_name"),
            "nx": nx,
            "ny": ny,
            "forecast_rows": len(forecasts),
            "available_from": available_dates[0],
            "available_to": available_dates[-1],
            "applied_from": available_dates[0],
            "applied_to": available_dates[horizon_days - 1],
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        return result
