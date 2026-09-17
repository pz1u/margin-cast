"""MarginCast 계산 도구가 공유하는 실행 기본값의 단일 정의."""

DEFAULT_HORIZON_DAYS = 14
FORECAST_DEFAULT_HORIZON_DAYS = 4
DEFAULT_SIMULATIONS = 10_000
DEFAULT_SEED = 42


def get_execution_defaults():
    """capabilities에 노출할 도구별 기본값을 새 객체로 반환한다."""
    return {
        "compare_price_strategies": {
            "horizon_days": DEFAULT_HORIZON_DAYS,
            "simulations": DEFAULT_SIMULATIONS,
            "seed": DEFAULT_SEED,
        },
        "compare_price_strategies_with_forecast": {
            "horizon_days": FORECAST_DEFAULT_HORIZON_DAYS,
            "simulations": DEFAULT_SIMULATIONS,
            "seed": DEFAULT_SEED,
        },
        "simulate_bundle_strategy": {
            "horizon_days": DEFAULT_HORIZON_DAYS,
            "simulations": DEFAULT_SIMULATIONS,
            "seed": DEFAULT_SEED,
        },
    }
