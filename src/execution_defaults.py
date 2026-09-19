"""MarginCast 계산 엔진이 공유하는 실행 기본값."""

from copy import deepcopy


DEFAULT_HORIZON_DAYS = 14
FORECAST_DEFAULT_HORIZON_DAYS = 4
DEFAULT_SIMULATIONS = 10_000
DEFAULT_SEED = 42

_EXECUTION_DEFAULTS = {
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


def get_execution_defaults():
    """호출자가 엔진 기본값을 변경하지 못하도록 복사본을 반환한다."""
    return deepcopy(_EXECUTION_DEFAULTS)
