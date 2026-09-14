import unittest

from src.decision_policy import assess_strategy, rank_strategies


def strategy(name, mean, p10, probability, confidence):
    return {
        "name": name,
        "is_reference": False,
        "profit_delta": {"mean": mean, "p10": p10},
        "success_probability": probability,
        "confidence": {"label": confidence, "score": 70},
    }


class DecisionPolicyTests(unittest.TestCase):
    def test_action_uses_probability_downside_and_confidence(self):
        recommend = assess_strategy(strategy("본 실행", 100000, 10000, 0.85, "MEDIUM"))
        experiment = assess_strategy(strategy("작은 실험", 150000, 20000, 0.95, "LOW"))
        hold = assess_strategy(strategy("보류", -10000, -50000, 0.4, "HIGH"))
        self.assertEqual(recommend["action"], "RECOMMEND")
        self.assertEqual(experiment["action"], "EXPERIMENT")
        self.assertEqual(hold["action"], "HOLD")
        self.assertFalse(recommend["is_probability"])

    def test_ranking_prefers_action_gate_before_expected_value(self):
        high_but_weak = strategy("고수익 가정", 300000, 50000, 0.95, "LOW")
        supported = strategy("검증된 전략", 100000, 10000, 0.85, "MEDIUM")
        ranking = rank_strategies([high_but_weak, supported])
        self.assertEqual(ranking[0]["name"], "검증된 전략")
        self.assertEqual(ranking[0]["decision"]["action"], "RECOMMEND")


if __name__ == "__main__":
    unittest.main()
