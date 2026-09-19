"""매장 주소의 실제 단기예보를 가격 의사결정 계산에 연결한다."""

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
from src.location_grid import KmaGridError, resolve_grid_coordinates
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
        menu_id,
        scenarios,
        location=None,
        address=None,
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
            raise ForecastDecisionError("INVALID_LOCATION", "location은 객체여야 합니다.")

        location_fields = set(location)
        if location_fields == {"address"}:
            address_query = str(location["address"] or "").strip()
            if not 1 <= len(address_query) <= 200:
                raise ForecastDecisionError(
                    "INVALID_LOCATION", "주소 검색 문자열은 1~200자로 입력하세요."
                )
            location_source = "address"
        elif location_fields == {"latitude", "longitude"}:
            location_source = "wgs84"
        elif location_fields == {"kma_nx", "kma_ny"}:
            location_source = "kma_grid"
        else:
            raise ForecastDecisionError(
                "INVALID_LOCATION",
                "주소, WGS84 위도·경도, KMA nx·ny 중 정확히 한 방식만 입력하세요.",
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
            if location_source == "address":
                resolved = self.geocoder(address_query, env_path=self.env_path)
                nx, ny = resolve_grid_coordinates(
                    latitude=resolved["latitude"],
                    longitude=resolved["longitude"],
                )
            elif location_source == "wgs84":
                nx, ny = resolve_grid_coordinates(
                    latitude=location["latitude"],
                    longitude=location["longitude"],
                )
            else:
                nx, ny = resolve_grid_coordinates(
                    nx=location["kma_nx"],
                    ny=location["kma_ny"],
                )
            forecasts = self.forecast_fetcher(nx, ny, env_path=self.env_path)
        except (KakaoConfigurationError, KmaConfigurationError) as error:
            raise ForecastDecisionError(
                "FORECAST_CONFIGURATION_ERROR", str(error)
            ) from error
        except KakaoGeocodingError as error:
            raise ForecastDecisionError(
                "FORECAST_LOOKUP_FAILED",
                "주소를 예보 위치로 변환하지 못했습니다.",
                retryable=True,
            ) from error
        except (KmaApiError, KmaGridError) as error:
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
        applied_from = available_dates[0]
        applied_to = available_dates[horizon_days - 1]
        result.setdefault("weather_context", {}).update(
            {
                "source": "kma_forecast",
                "uses_future_forecast": True,
                "menu_specific_causal_effect_validated": False,
                "applied_from": applied_from,
                "applied_to": applied_to,
            }
        )
        result["weather"] = {
            "source": "kma_short_term_forecast",
            "location_source": location_source,
            "nx": nx,
            "ny": ny,
            "forecast_rows": len(forecasts),
            "available_from": available_dates[0],
            "available_to": available_dates[-1],
            "applied_from": applied_from,
            "applied_to": applied_to,
            "menu_specific_causal_effect_validated": False,
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        return result
