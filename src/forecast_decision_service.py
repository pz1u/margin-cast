"""주소·좌표·기상청 격자의 실제 단기예보를 가격 의사결정 계산에 연결한다."""

from datetime import datetime
from pathlib import Path

from src.decision_service import MarginCastDecisionService
from src.execution_defaults import (
    DEFAULT_SEED,
    DEFAULT_SIMULATIONS,
    FORECAST_DEFAULT_HORIZON_DAYS,
)
from src.kakao_geocoding import (
    KakaoConfigurationError,
    KakaoGeocodingError,
    geocode_address,
)
from src.location_grid import KmaGridError
from src.weather_forecast import (
    KmaApiError,
    KmaConfigurationError,
    fetch_village_forecast,
    resolve_forecast_location,
)


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
        location=None,
        address=None,
        menu_id,
        scenarios,
        horizon_days=FORECAST_DEFAULT_HORIZON_DAYS,
        simulations=DEFAULT_SIMULATIONS,
        seed=DEFAULT_SEED,
    ):
        if location is not None and address is not None:
            raise ForecastDecisionError(
                "INVALID_LOCATION", "location과 address를 함께 입력할 수 없습니다."
            )
        if address is not None:
            location = {"address": address}
        if not isinstance(location, dict):
            raise ForecastDecisionError(
                "INVALID_LOCATION", "location은 주소, WGS84 좌표 또는 KMA 격자 객체여야 합니다."
            )

        allowed_location_fields = {"address", "latitude", "longitude", "nx", "ny"}
        unknown = sorted(set(location) - allowed_location_fields)
        if unknown:
            raise ForecastDecisionError(
                "INVALID_LOCATION",
                "location에 지원하지 않는 필드가 있습니다.",
                {"unknown_fields": unknown},
            )
        has_address = "address" in location
        has_wgs84 = "latitude" in location or "longitude" in location
        has_grid = "nx" in location or "ny" in location
        if sum((has_address, has_wgs84, has_grid)) != 1:
            raise ForecastDecisionError(
                "INVALID_LOCATION",
                "주소, WGS84 위도·경도, KMA nx·ny 중 정확히 하나를 입력하세요.",
            )
        if has_address:
            normalized_address = str(location.get("address") or "").strip()
            if not 1 <= len(normalized_address) <= 200:
                raise ForecastDecisionError(
                    "INVALID_ADDRESS", "매장 주소는 1~200자로 입력하세요."
                )
            resolver_arguments = {"address": normalized_address}
            location_source = "address"
        elif has_wgs84:
            if set(location) != {"latitude", "longitude"}:
                raise ForecastDecisionError(
                    "INVALID_LOCATION", "WGS84 위치에는 latitude와 longitude가 모두 필요합니다."
                )
            resolver_arguments = {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
            }
            location_source = "wgs84"
        else:
            if set(location) != {"nx", "ny"}:
                raise ForecastDecisionError(
                    "INVALID_LOCATION", "KMA 격자 위치에는 nx와 ny가 모두 필요합니다."
                )
            resolver_arguments = {"nx": location["nx"], "ny": location["ny"]}
            location_source = "kma_grid"
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
            nx, ny, _ = resolve_forecast_location(
                **resolver_arguments,
                env_path=self.env_path,
                geocoder=self.geocoder,
            )
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
            "location_source": location_source,
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
