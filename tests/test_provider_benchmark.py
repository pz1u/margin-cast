import unittest

from scripts.benchmark_agent_providers import (
    estimate_luna_cost,
    percentile,
)


class ProviderBenchmarkTests(unittest.TestCase):
    def test_luna_cost_uses_uncached_cached_and_output_rates(self):
        usage = {
            "input_tokens": 1_000,
            "cached_input_tokens": 200,
            "output_tokens": 100,
            "total_tokens": 1_100,
        }

        self.assertEqual(
            estimate_luna_cost("gpt-5.6-luna", usage),
            0.000284,
        )
        self.assertIsNone(estimate_luna_cost("another-model", usage))

    def test_percentile_uses_nearest_rank_for_small_demo_samples(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0]

        self.assertEqual(percentile(values, 0.50), 30.0)
        self.assertEqual(percentile(values, 0.95), 50.0)
        self.assertIsNone(percentile([], 0.95))


if __name__ == "__main__":
    unittest.main()