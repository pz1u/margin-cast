"""에이전트나 API가 계산 엔진을 안전하게 호출할 수 있는 서비스 경계."""

from pathlib import Path

import pandas as pd

try:
    from .confidence_score import calculate_confidence
    from .decision_policy import rank_strategies
    from .estimate_elasticity import estimate_price_elasticity
    from .prepare_analysis_data import load_observed_tables
    from .simulate_bundle import build_bundle_evidence, simulate_bundle
    from .simulate_strategy import build_reference_forecast, simulate_scenarios
except ImportError:
    from confidence_score import calculate_confidence
    from decision_policy import rank_strategies
    from estimate_elasticity import estimate_price_elasticity
    from prepare_analysis_data import load_observed_tables
    from simulate_bundle import build_bundle_evidence, simulate_bundle
    from simulate_strategy import build_reference_forecast, simulate_scenarios


SERVICE_VERSION = "0.2.0"
MIN_SIMULATIONS = 100
MAX_SIMULATIONS = 50_000
MAX_SCENARIOS = 8


class DecisionServiceError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {} if details is None else details


class MarginCastDecisionService:
    def __init__(self, panel_path=None, data_dir=None):
        root = Path(__file__).resolve().parents[1]
        self.panel_path = Path(panel_path or root / "data" / "processed" / "demand_panel.csv")
        self.data_dir = Path(data_dir or self.panel_path.parents[1])
        self._panel = None
        self._elasticity_cache = {}
        self._reference_cache = {}

    def _load_panel(self):
        if self._panel is None:
            if not self.panel_path.is_file():
                raise DecisionServiceError(
                    "PANEL_NOT_FOUND",
                    "분석 패널이 없습니다. 먼저 src.prepare_analysis_data를 실행하세요.",
                    {"panel_path": str(self.panel_path)},
                )
            panel = pd.read_csv(self.panel_path, encoding="utf-8-sig")
            required = {
                "menu_id",
                "menu_name",
                "split",
                "day_index",
                "initial_list_price",
                "offered_list_price",
                "units_sold",
            }
            missing = sorted(required - set(panel.columns))
            if missing:
                raise DecisionServiceError(
                    "INVALID_PANEL",
                    "분석 패널에 필수 컬럼이 없습니다.",
                    {"missing_columns": missing},
                )
            self._panel = panel
        return self._panel

    def _supported_menus(self):
        panel = self._load_panel()
        train = panel[panel["split"] == "train"]
        price_counts = train.groupby("menu_id")["offered_list_price"].nunique()
        supported_ids = set(price_counts[price_counts >= 2].index)
        menus = (
            panel[["menu_id", "menu_name", "initial_list_price"]]
            .drop_duplicates("menu_id")
            .sort_values("menu_id")
        )
        return [
            {
                "menu_id": row.menu_id,
                "menu_name": row.menu_name,
                "baseline_price": int(row.initial_list_price),
            }
            for row in menus.itertuples(index=False)
            if row.menu_id in supported_ids
        ]

    def get_capabilities(self):
        panel = self._load_panel()
        supported = self._supported_menus()
        return {
            "status": "ok",
            "service": "MarginCast Decision Engine",
            "version": SERVICE_VERSION,
            "data": {
                "start_day_index": int(panel["day_index"].min()),
                "end_day_index": int(panel["day_index"].max()),
                "rows": int(len(panel)),
                "ground_truth_used": False,
            },
            "supported_menus": supported,
            "operations": [
                "get_capabilities",
                "compare_price_strategies",
                "simulate_bundle_strategy",
            ],
            "limits": {
                "horizon_days": {"minimum": 1, "maximum": int(panel["day_index"].nunique())},
                "simulations": {"minimum": MIN_SIMULATIONS, "maximum": MAX_SIMULATIONS},
                "scenarios": {"minimum": 1, "maximum": MAX_SCENARIOS},
            },
            "limitations": [
                "가격탄력성은 학습 구간에 두 개 이상의 정가가 관측된 메뉴만 지원한다.",
                "미래 날씨 입력 전까지 최근 관측 문맥을 재사용한다.",
                "세트 전략의 신규 수요와 잠식 효과는 사용자가 명시한 가정으로 계산한다.",
            ],
        }

    def _validate_compare_request(self, menu_id, scenarios, horizon_days, simulations, seed):
        supported_ids = {row["menu_id"] for row in self._supported_menus()}
        if menu_id not in supported_ids:
            raise DecisionServiceError(
                "UNSUPPORTED_MENU",
                f"{menu_id}는 가격탄력성 근거가 부족해 전략 비교를 지원하지 않습니다.",
                {"supported_menu_ids": sorted(supported_ids)},
            )
        if isinstance(horizon_days, bool) or not isinstance(horizon_days, int):
            raise DecisionServiceError("INVALID_HORIZON", "horizon_days는 정수여야 합니다.")
        maximum_days = int(self._load_panel()["day_index"].nunique())
        if not 1 <= horizon_days <= maximum_days:
            raise DecisionServiceError(
                "INVALID_HORIZON",
                f"horizon_days는 1~{maximum_days} 범위여야 합니다.",
            )
        if isinstance(simulations, bool) or not isinstance(simulations, int):
            raise DecisionServiceError("INVALID_SIMULATIONS", "simulations는 정수여야 합니다.")
        if not MIN_SIMULATIONS <= simulations <= MAX_SIMULATIONS:
            raise DecisionServiceError(
                "INVALID_SIMULATIONS",
                f"simulations는 {MIN_SIMULATIONS:,}~{MAX_SIMULATIONS:,} 범위여야 합니다.",
            )
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
            raise DecisionServiceError("INVALID_SEED", "seed는 0~2^32-1 범위의 정수여야 합니다.")
        if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= MAX_SCENARIOS:
            raise DecisionServiceError(
                "INVALID_SCENARIOS",
                f"scenarios는 1~{MAX_SCENARIOS}개의 배열이어야 합니다.",
            )
        names = set()
        for position, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict):
                raise DecisionServiceError(
                    "INVALID_SCENARIO", "각 시나리오는 객체여야 합니다.", {"index": position}
                )
            required = {"name", "list_price", "discount"}
            missing = sorted(required - set(scenario))
            if missing:
                raise DecisionServiceError(
                    "INVALID_SCENARIO",
                    "시나리오에 필수 값이 없습니다.",
                    {"index": position, "missing_fields": missing},
                )
            name = scenario["name"]
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 50:
                raise DecisionServiceError(
                    "INVALID_SCENARIO", "시나리오 이름은 1~50자의 문자열이어야 합니다.", {"index": position}
                )
            if name in names:
                raise DecisionServiceError("INVALID_SCENARIO", "시나리오 이름은 중복될 수 없습니다.")
            names.add(name)
            price, discount = scenario["list_price"], scenario["discount"]
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (price, discount)):
                raise DecisionServiceError(
                    "INVALID_SCENARIO", "정가와 할인액은 원 단위 정수여야 합니다.", {"index": position}
                )
            if not 1_000 <= price <= 100_000 or not 0 <= discount < price:
                raise DecisionServiceError(
                    "INVALID_SCENARIO",
                    "정가는 1,000~100,000원, 할인액은 0 이상 정가 미만이어야 합니다.",
                    {"index": position},
                )

    def compare_price_strategies(
        self,
        menu_id,
        scenarios,
        horizon_days=14,
        simulations=10_000,
        seed=42,
    ):
        self._validate_compare_request(menu_id, scenarios, horizon_days, simulations, seed)
        panel = self._load_panel()
        if menu_id not in self._elasticity_cache:
            self._elasticity_cache[menu_id] = estimate_price_elasticity(panel, menu_id=menu_id)
        reference_key = (menu_id, horizon_days)
        if reference_key not in self._reference_cache:
            self._reference_cache[reference_key] = build_reference_forecast(
                panel, menu_id=menu_id, horizon_days=horizon_days
            )
        reference, baseline_price, _ = self._reference_cache[reference_key]
        normalized = [dict(value) for value in scenarios]
        reference_count = sum(
            value["list_price"] - value["discount"] == baseline_price
            and value["discount"] == 0
            for value in normalized
        )
        if reference_count == 0:
            normalized.insert(
                0,
                {"name": "현재 가격", "list_price": baseline_price, "discount": 0},
            )
        elif reference_count > 1:
            raise DecisionServiceError(
                "INVALID_SCENARIOS", "현재 가격과 동일한 기준 시나리오는 하나만 입력할 수 있습니다."
            )
        if len(normalized) == 1 and reference_count == 1:
            raise DecisionServiceError(
                "INVALID_SCENARIOS", "현재 가격과 비교할 대안을 하나 이상 입력해야 합니다."
            )

        results = simulate_scenarios(
            reference,
            self._elasticity_cache[menu_id],
            normalized,
            baseline_price,
            simulations=simulations,
            seed=seed,
        )
        for scenario, result in zip(normalized, results):
            result["confidence"] = (
                None
                if result["is_reference"]
                else calculate_confidence(
                    panel,
                    self._elasticity_cache[menu_id],
                    scenario,
                    "observed_history",
                )
            )
        ranked = rank_strategies(results)
        candidates = [row for row in results if not row["is_reference"]]
        recommended = max(candidates, key=lambda row: row["contribution_profit"]["mean"])
        for row in results:
            row["downside_risk"] = bool(row.get("decision", {}).get("downside_risk", False))
        return {
            "status": "ok",
            "request": {
                "menu_id": menu_id,
                "horizon_days": horizon_days,
                "simulations": simulations,
                "seed": seed,
            },
            "model": {
                "elasticity": self._elasticity_cache[menu_id]["elasticity"],
                "elasticity_standard_error": self._elasticity_cache[menu_id]["robust_standard_error"],
                "ground_truth_used": False,
            },
            "strategies": results,
            "highest_expected_profit": {
                "name": recommended["name"],
                "expected_contribution_profit": recommended["contribution_profit"]["mean"],
                "success_probability": recommended["success_probability"],
                "downside_risk": recommended["downside_risk"],
                "confidence": recommended["confidence"],
            },
            "decision_ranking": [
                {
                    "rank": index,
                    "name": row["name"],
                    **row["decision"],
                }
                for index, row in enumerate(ranked, start=1)
            ],
            "recommended_action": {
                "name": ranked[0]["name"],
                "action": ranked[0]["decision"]["action"],
                "reason": ranked[0]["decision"]["reason"],
            },
            "interpretation_notes": [
                "highest_expected_profit은 기대값 기준 정렬이며 최종 실행 결정은 아니다.",
                "decision_ranking은 기대이익·개선확률·80% 하한·신뢰도를 함께 반영한다.",
                "downside_risk는 현재 대비 기여이익 차이의 10백분위가 0보다 작은 경우 true다.",
                "미래 날씨 예보가 없으므로 최근 관측 문맥을 재사용했다.",
                "confidence는 근거 품질 점수이며 success_probability와 별개다.",
            ],
        }

    def simulate_bundle_strategy(
        self,
        scenario,
        horizon_days=14,
        simulations=10_000,
        seed=42,
    ):
        if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or horizon_days < 1:
            raise DecisionServiceError("INVALID_HORIZON", "horizon_days는 1 이상의 정수여야 합니다.")
        if (
            isinstance(simulations, bool)
            or not isinstance(simulations, int)
            or not MIN_SIMULATIONS <= simulations <= MAX_SIMULATIONS
        ):
            raise DecisionServiceError(
                "INVALID_SIMULATIONS",
                f"simulations는 {MIN_SIMULATIONS:,}~{MAX_SIMULATIONS:,} 범위여야 합니다.",
            )
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
            raise DecisionServiceError("INVALID_SEED", "seed는 0~2^32-1 범위의 정수여야 합니다.")
        tables = load_observed_tables(self.data_dir)
        evidence = build_bundle_evidence(tables)
        panel = self._load_panel().copy()
        panel["date"] = pd.to_datetime(panel["date"])
        bundle_start = tables["bundles"]["start_date"].min()
        clean = panel[panel["date"] < bundle_start]
        recent_dates = sorted(clean["date"].unique())[-14:]
        baseline_daily_profit = (
            clean[clean["date"].isin(recent_dates)]
            .groupby("date")["contribution_profit"]
            .sum()
            .to_numpy()
        )
        result = simulate_bundle(
            evidence,
            scenario,
            horizon_days=horizon_days,
            simulations=simulations,
            seed=seed,
            baseline_daily_profit=baseline_daily_profit,
        )
        result["is_reference"] = False
        decision = rank_strategies([result])[0]["decision"]
        return {
            "status": "ok",
            "request": {
                "horizon_days": horizon_days,
                "simulations": simulations,
                "seed": seed,
            },
            "strategy": result,
            "decision": decision,
            "interpretation_notes": [
                "세트 전환율·신규 수요율·잠식률은 사용자가 제공한 시나리오 가정이다.",
                "근거 신뢰도가 LOW이면 기대이익과 개선확률이 높아도 EXPERIMENT로 제한한다.",
                "계산과 의사결정에 ground_truth.json을 사용하지 않았다.",
            ],
        }
