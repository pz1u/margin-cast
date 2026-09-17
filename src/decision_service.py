"""에이전트나 API가 계산 엔진을 안전하게 호출할 수 있는 서비스 경계."""

from pathlib import Path

import pandas as pd

try:
    from .execution_defaults import (
        DEFAULT_HORIZON_DAYS,
        DEFAULT_SEED,
        DEFAULT_SIMULATIONS,
        get_execution_defaults,
    )
    from .evidence_quality import calculate_evidence_quality
    from .decision_policy import rank_strategies
    from .estimate_elasticity import estimate_price_elasticity
    from .prepare_analysis_data import load_observed_tables
    from .simulate_bundle import build_bundle_evidence, simulate_bundle
    from .simulate_strategy import build_reference_forecast, simulate_scenarios
except ImportError:
    from execution_defaults import (
        DEFAULT_HORIZON_DAYS,
        DEFAULT_SEED,
        DEFAULT_SIMULATIONS,
        get_execution_defaults,
    )
    from evidence_quality import calculate_evidence_quality
    from decision_policy import rank_strategies
    from estimate_elasticity import estimate_price_elasticity
    from prepare_analysis_data import load_observed_tables
    from simulate_bundle import build_bundle_evidence, simulate_bundle
    from simulate_strategy import build_reference_forecast, simulate_scenarios


SERVICE_VERSION = "0.5.0"
MIN_SIMULATIONS = 100
MAX_SIMULATIONS = 50_000
MAX_SCENARIOS = 8
REQUIRED_UNIQUE_PRICE_LEVELS = 2

DATA_PROVENANCE = {
    "source_type": "synthetic_pos",
    "dataset_version": "synthetic-pos-v2",
    "uses_actual_store_data": False,
    "label": "SYNTHETIC_DATA_PROTOTYPE",
    "warning": (
        "현재 예측은 합성 POS 데이터에서 학습한 프로토타입 결과이며 실제 매장 성과를 보증하지 않는다."
    ),
}


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

    def _menu_support_profiles(self):
        panel = self._load_panel()
        train = panel[panel["split"] == "train"]
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
                "supported": len(
                    train.loc[
                        train["menu_id"] == row.menu_id, "offered_list_price"
                    ].unique()
                )
                >= REQUIRED_UNIQUE_PRICE_LEVELS,
                "observed_price_levels": sorted(
                    int(value)
                    for value in train.loc[
                        train["menu_id"] == row.menu_id, "offered_list_price"
                    ].unique()
                ),
                "required_unique_price_levels": REQUIRED_UNIQUE_PRICE_LEVELS,
            }
            for row in menus.itertuples(index=False)
        ]

    def _supported_menus(self):
        return [
            {key: value for key, value in profile.items() if key != "supported"}
            for profile in self._menu_support_profiles()
            if profile["supported"]
        ]

    def _data_provenance(self):
        panel = self._load_panel()
        train = panel[panel["split"] == "train"]
        result = dict(DATA_PROVENANCE)
        result["training_rows"] = int(len(train))
        if "date" in train:
            dates = pd.to_datetime(train["date"])
            result["training_period"] = {
                "start": dates.min().strftime("%Y-%m-%d"),
                "end": dates.max().strftime("%Y-%m-%d"),
            }
        return result

    def get_capabilities(self):
        panel = self._load_panel()
        supported = self._supported_menus()
        unsupported = [
            {
                **{key: value for key, value in profile.items() if key != "supported"},
                "reason_code": "INSUFFICIENT_PRICE_VARIATION",
                "next_step": (
                    "기준 가격 외 최소 한 개 가격을 별도 기간에 운영해 두 개 이상의 가격 수준을 확보하세요."
                ),
            }
            for profile in self._menu_support_profiles()
            if not profile["supported"]
        ]
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
            "data_provenance": self._data_provenance(),
            "supported_menus": supported,
            "unsupported_menus": unsupported,
            "operations": [
                "get_margincast_capabilities",
                "compare_price_strategies",
                "compare_price_strategies_with_forecast",
                "simulate_bundle_strategy",
                "create_experiment_plan",
                "list_pending_experiments",
                "record_experiment_result",
                "get_feedback_summary",
            ],
            "execution_defaults": get_execution_defaults(),
            "limits": {
                "horizon_days": {"minimum": 1, "maximum": int(panel["day_index"].nunique())},
                "simulations": {"minimum": MIN_SIMULATIONS, "maximum": MAX_SIMULATIONS},
                "scenarios": {"minimum": 1, "maximum": MAX_SCENARIOS},
            },
            "limitations": [
                "가격탄력성은 학습 구간에 두 개 이상의 가격 수준이 관측된 메뉴만 지원한다.",
                "미래 날씨 입력 전까지 최근 관측 문맥을 재사용한다.",
                "세트 전략의 신규 수요와 잠식 효과는 사용자가 명시한 가정으로 계산한다.",
                "근거 품질 점수는 실제 매장 결과로 아직 보정되지 않은 휴리스틱이다.",
            ],
        }

    def _validate_compare_request(self, menu_id, scenarios, horizon_days, simulations, seed):
        profiles = {row["menu_id"]: row for row in self._menu_support_profiles()}
        supported_ids = {menu_id for menu_id, row in profiles.items() if row["supported"]}
        if menu_id not in supported_ids:
            profile = profiles.get(menu_id)
            details = {
                "supported_menu_ids": sorted(supported_ids),
                "reason_code": (
                    "INSUFFICIENT_PRICE_VARIATION" if profile else "UNKNOWN_MENU"
                ),
                "required_unique_price_levels": REQUIRED_UNIQUE_PRICE_LEVELS,
                "observed_price_levels": profile["observed_price_levels"] if profile else [],
                "next_step": (
                    "기준 가격 외 최소 한 개 가격을 별도 기간에 운영해 두 개 이상의 가격 수준을 확보하세요."
                    if profile
                    else "capabilities의 supported_menus와 unsupported_menus에서 메뉴 ID를 확인하세요."
                ),
            }
            raise DecisionServiceError(
                "UNSUPPORTED_MENU",
                f"{menu_id}는 가격 변화 근거가 부족해 전략 비교를 지원하지 않습니다.",
                details,
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
        horizon_days=DEFAULT_HORIZON_DAYS,
        simulations=DEFAULT_SIMULATIONS,
        seed=DEFAULT_SEED,
        forecasts=None,
    ):
        self._validate_compare_request(menu_id, scenarios, horizon_days, simulations, seed)
        panel = self._load_panel()
        if menu_id not in self._elasticity_cache:
            self._elasticity_cache[menu_id] = estimate_price_elasticity(panel, menu_id=menu_id)
        if forecasts is None:
            reference_key = (menu_id, horizon_days)
            if reference_key not in self._reference_cache:
                self._reference_cache[reference_key] = build_reference_forecast(
                    panel, menu_id=menu_id, horizon_days=horizon_days
                )
            reference, baseline_price, context_source = self._reference_cache[reference_key]
        else:
            reference, baseline_price, context_source = build_reference_forecast(
                panel,
                menu_id=menu_id,
                horizon_days=horizon_days,
                forecasts=forecasts,
            )
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
            result["evidence_quality"] = (
                None
                if result["is_reference"]
                else calculate_evidence_quality(
                    panel,
                    self._elasticity_cache[menu_id],
                    scenario,
                    context_source,
                )
            )
        ranked = rank_strategies(results)
        candidates = [row for row in results if not row["is_reference"]]
        recommended = max(candidates, key=lambda row: row["contribution_profit"]["mean"])
        for row in results:
            row["downside_risk"] = bool(row.get("decision", {}).get("downside_risk", False))
        return {
            "status": "ok",
            "engine_version": SERVICE_VERSION,
            "request": {
                "menu_id": menu_id,
                "horizon_days": horizon_days,
                "simulations": simulations,
                "seed": seed,
            },
            "weather_context_source": context_source,
            "weather_context": {
                "source": context_source,
                "uses_future_forecast": context_source == "kma_forecast",
                "menu_specific_causal_effect_validated": False,
                "interpretation": (
                    "날씨는 미래 수요 문맥으로 사용되며 메뉴별 날씨 인과효과를 입증하지 않는다."
                ),
            },
            "data_provenance": self._data_provenance(),
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
                "evidence_quality": recommended["evidence_quality"],
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
                "decision_ranking은 기대이익·개선확률·80% 하한·근거 품질을 함께 반영한다.",
                "downside_risk는 현재 대비 기여이익 차이의 10백분위가 0보다 작은 경우 true다.",
                (
                    "기상청 단기예보를 미래 날짜의 영업시간 문맥으로 사용했다."
                    if context_source == "kma_forecast"
                    else "미래 날씨 예보가 없으므로 최근 관측 문맥을 재사용했다."
                ),
                "evidence_quality는 실제 매장 결과로 아직 보정되지 않은 휴리스틱이며 success_probability와 별개다.",
                "날씨는 미래 수요 문맥으로 사용되며 메뉴별 날씨 인과효과를 입증하지 않는다.",
            ],
        }

    def simulate_bundle_strategy(
        self,
        main_menu_id,
        component_menu_ids,
        scenario,
        horizon_days=DEFAULT_HORIZON_DAYS,
        simulations=DEFAULT_SIMULATIONS,
        seed=DEFAULT_SEED,
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
        if not isinstance(main_menu_id, str) or not main_menu_id.strip():
            raise DecisionServiceError("INVALID_BUNDLE_MENUS", "main_menu_id를 입력하세요.")
        if (
            not isinstance(component_menu_ids, list)
            or not 1 <= len(component_menu_ids) <= 4
            or any(not isinstance(value, str) or not value.strip() for value in component_menu_ids)
        ):
            raise DecisionServiceError(
                "INVALID_BUNDLE_MENUS",
                "component_menu_ids는 1~4개의 메뉴 ID 배열이어야 합니다.",
            )
        tables = load_observed_tables(self.data_dir)
        try:
            evidence = build_bundle_evidence(
                tables,
                main_menu_id=main_menu_id,
                component_menu_ids=component_menu_ids,
            )
        except (KeyError, ValueError) as error:
            raise DecisionServiceError(
                "INVALID_BUNDLE_MENUS", str(error)
            ) from error
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
            "engine_version": SERVICE_VERSION,
            "request": {
                "main_menu_id": main_menu_id,
                "component_menu_ids": list(component_menu_ids),
                "horizon_days": horizon_days,
                "simulations": simulations,
                "seed": seed,
            },
            "data_provenance": self._data_provenance(),
            "strategy": result,
            "decision": decision,
            "interpretation_notes": [
                "세트 전환율·신규 수요율·잠식률은 사용자가 제공한 시나리오 가정이다.",
                "근거 품질이 LOW이면 기대이익과 개선확률이 높아도 EXPERIMENT로 제한한다.",
                "근거 품질 점수는 실제 매장 결과로 아직 보정되지 않은 휴리스틱이다.",
                "계산과 의사결정에 ground_truth.json을 사용하지 않았다.",
            ],
        }
