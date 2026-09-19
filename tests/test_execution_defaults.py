import inspect
import unittest

from src.decision_service import MarginCastDecisionService
from src.execution_defaults import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_SEED,
    DEFAULT_SIMULATIONS,
    FORECAST_DEFAULT_HORIZON_DAYS,
)
from src.forecast_decision_service import ForecastDecisionService
from src.simulate_bundle import run_bundle_simulation, simulate_bundle
from src.simulate_strategy import build_reference_forecast, run_simulation, simulate_scenarios


class ExecutionDefaultsTests(unittest.TestCase):
    def assert_default(self, callable_, parameter_name, expected):
        actual = inspect.signature(callable_).parameters[parameter_name].default
        self.assertEqual(actual, expected)

    def test_engine_entry_points_share_execution_default_constants(self):
        default_horizon_callables = (
            MarginCastDecisionService.compare_price_strategies,
            MarginCastDecisionService.simulate_bundle_strategy,
            build_reference_forecast,
            run_simulation,
            simulate_bundle,
            run_bundle_simulation,
        )
        for callable_ in default_horizon_callables:
            with self.subTest(callable=callable_.__qualname__, parameter="horizon_days"):
                self.assert_default(callable_, "horizon_days", DEFAULT_HORIZON_DAYS)

        self.assert_default(
            ForecastDecisionService.compare_price_strategies,
            "horizon_days",
            FORECAST_DEFAULT_HORIZON_DAYS,
        )
        for callable_ in (
            MarginCastDecisionService.compare_price_strategies,
            MarginCastDecisionService.simulate_bundle_strategy,
            ForecastDecisionService.compare_price_strategies,
            simulate_scenarios,
            run_simulation,
            simulate_bundle,
            run_bundle_simulation,
        ):
            with self.subTest(callable=callable_.__qualname__, parameter="simulations"):
                self.assert_default(callable_, "simulations", DEFAULT_SIMULATIONS)
            with self.subTest(callable=callable_.__qualname__, parameter="seed"):
                self.assert_default(callable_, "seed", DEFAULT_SEED)


if __name__ == "__main__":
    unittest.main()
