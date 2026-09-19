(function initializeMarginCastStoreModel(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MarginCastStoreModel = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createApi() {
  "use strict";

  const ANALYSIS_STATUS = Object.freeze({
    ANALYZABLE: {
      label: "분석 가능",
      tone: "ok",
      message: "가격 시뮬레이션을 사용할 수 있습니다.",
    },
    INSUFFICIENT_PRICE_VARIATION: {
      label: "가격 변화 데이터 부족",
      tone: "wait",
      message: "가격을 바꿔 판매한 기록이 부족해 가격 시뮬레이션을 사용할 수 없습니다.",
    },
    DATA_COLLECTING: {
      label: "데이터 수집 중",
      tone: "wait",
      message:
        "아직 판매·가격 변경 데이터가 충분하지 않아 가격 전략 시뮬레이션을 사용할 수 없습니다.",
    },
  });
  // 가정의 출처. 계획용 가정은 실제 POS 데이터에서 추정한 값이 아니다.
  const SOURCE_LABELS = Object.freeze({
    USER: "직접 입력한 가정",
    ENGINE: "엔진 값",
    DEFAULT: "계획용 가정",
  });
  const COST_SOURCE_LABELS = Object.freeze({
    USER: "직접 수정한 원가",
    POS_HISTORY: "POS 기준 원가",
  });
  const PLAN_DISCLAIMER = "계획용 가정이며 실제 POS 데이터에서 추정된 값이 아닙니다.";
  const DECISION_GUIDANCE = Object.freeze({
    RECOMMEND: "실행을 검토해 볼 만한 전략입니다.",
    EXPERIMENT: "전면 적용보다 작게 시험해 본 뒤 결정하세요.",
    HOLD: "지금은 바꾸지 않는 편이 안전합니다.",
  });
  const EFFECT_GROUPS = Object.freeze({
    take_rate: "세트 전환 효과",
    copurchase_take_rate: "세트 전환 효과",
    incremental_demand_rate: "신규 수요 효과",
    cannibalization_rate: "다른 메뉴 잠식 효과",
  });
  const RATE_LABELS = Object.freeze({
    take_rate: "세트를 고르는 단품 고객 비율",
    copurchase_take_rate: "세트로 바꾸는 기존 동시구매 고객 비율",
    incremental_demand_rate: "세트 때문에 새로 생기는 주문 비율",
    cannibalization_rate: "다른 메뉴에서 옮겨 오는 주문 비율",
  });
  const CATEGORY_LABELS = Object.freeze({ MAIN: "메인", SIDE: "사이드", DRINK: "음료" });
  const CHANNEL_LABELS = Object.freeze({ STORE: "매장", DELIVERY: "배달" });
  const BUNDLE_RATE_FIELDS = Object.freeze([
    "take_rate",
    "copurchase_take_rate",
    "incremental_demand_rate",
    "cannibalization_rate",
  ]);
  const MAX_SCENARIOS = 8;

  function isDebugMode(search) {
    return new URLSearchParams(search || "").get("debug") === "1";
  }

  /** 일반 화면과 debug 화면에서 보일 필드 묶음. 일반 사용자는 실행 설정과 내부 ID를 보지 않는다. */
  function fieldVisibility(debug) {
    return {
      simulations: Boolean(debug),
      seed: Boolean(debug),
      menuId: Boolean(debug),
      modelParameters: Boolean(debug),
    };
  }

  function analysisInfo(status) {
    return (
      ANALYSIS_STATUS[status] || {
        label: "확인 필요",
        tone: "wait",
        message: "분석 상태를 확인하지 못했습니다.",
      }
    );
  }

  function decisionGuidance(action) {
    return DECISION_GUIDANCE[action] || "";
  }

  /** 근거 품질을 일반 용어로 바꾼다. 미보정은 실제 결과로 아직 검증되지 않았다는 뜻이다. */
  function evidenceLabel(quality) {
    if (!quality) return "-";
    const labels = { HIGH: "높음", MEDIUM: "보통", LOW: "낮음" };
    const level = labels[quality.label] || "확인 필요";
    return quality.validation?.empirically_calibrated === false
      ? `${level} (실제 결과로 검증 전)`
      : level;
  }

  /** 엔진 설명문 속 등급 코드를 화면 용어로 바꾼다. 문장의 의미와 숫자는 그대로 둔다. */
  function plainText(text) {
    const levels = { HIGH: "높음", MEDIUM: "보통", LOW: "낮음" };
    return String(text ?? "").replace(/\b(HIGH|MEDIUM|LOW)\b/g, (code) => levels[code]);
  }

  function isPriceAnalyzable(menu) {
    return Boolean(menu) && menu.analysis?.status === "ANALYZABLE";
  }

  function priceMenus(menus) {
    return menus.filter(isPriceAnalyzable);
  }

  /** 세트에는 POS에서 가져온 메뉴만 쓴다. 신규 메뉴는 판매 이력이 없어 제외한다. */
  function bundleMenus(menus) {
    return menus.filter((menu) => menu.source === "POS");
  }

  function bundleMainMenus(menus) {
    return bundleMenus(menus).filter((menu) => menu.category === "MAIN");
  }

  function executionDefaults(capabilities, tool) {
    const defaults = capabilities?.execution_defaults?.[tool];
    if (!defaults) return null;
    return {
      horizon_days: defaults.horizon_days,
      simulations: defaults.simulations,
      seed: defaults.seed,
    };
  }

  function scenarioName(listPrice, discount, baselinePrice) {
    const parts = [];
    const diff = listPrice - baselinePrice;
    if (diff !== 0) {
      parts.push(`${Math.abs(diff).toLocaleString("ko-KR")}원 ${diff > 0 ? "인상" : "인하"}`);
    }
    if (discount > 0) parts.push(`${discount.toLocaleString("ko-KR")}원 할인`);
    return parts.length ? parts.join(" · ") : "가격 유지";
  }

  /** 시나리오 이름은 화면이 정하고 이름 중복은 번호로 구분한다. */
  function priceScenarios(rows, baselinePrice) {
    const seen = new Map();
    return rows.map((row) => {
      const base = scenarioName(row.list_price, row.discount, baselinePrice);
      const count = (seen.get(base) || 0) + 1;
      seen.set(base, count);
      return {
        name: count === 1 ? base : `${base} (${count})`,
        list_price: row.list_price,
        discount: row.discount,
      };
    });
  }

  /** 실행 설정은 엔진 기본값(DEFAULT)을 쓰고 debug 입력이 있을 때만 덮어쓴다. */
  function executionSettings(defaults, horizonDays, overrides = {}) {
    if (!defaults) throw new Error("엔진 실행 기본값을 불러오지 못했습니다.");
    const settings = { horizon_days: horizonDays };
    ["simulations", "seed"].forEach((name) => {
      const value = overrides[name];
      settings[name] = Number.isInteger(value) ? value : defaults[name];
    });
    return settings;
  }

  function buildPriceRequest({ menuId, rows, baselinePrice, defaults, horizonDays, overrides }) {
    return {
      menu_id: menuId,
      scenarios: priceScenarios(rows, baselinePrice),
      ...executionSettings(defaults, horizonDays, overrides),
    };
  }

  function buildBundleRequest({
    mainMenuId,
    componentMenuIds,
    name,
    bundlePrice,
    assumptions,
    defaults,
    horizonDays,
    overrides,
  }) {
    const scenario = { name, bundle_price: bundlePrice };
    BUNDLE_RATE_FIELDS.forEach((field) => {
      scenario[field] = assumptions[field];
    });
    return {
      main_menu_id: mainMenuId,
      component_menu_ids: [...componentMenuIds],
      scenario,
      ...executionSettings(defaults, horizonDays, overrides),
    };
  }

  /** 입력 화면에서만 쓰는 즉시 미리보기. 저장된 값과 시뮬레이션은 서버의 같은 식을 사용한다. */
  function unitEconomics(fees, { listPrice, ingredientCost, channels, deliveryShare }) {
    if (!(listPrice > 0)) return null;
    const rate = (channel) => fees[channel].platform + fees[channel].payment;
    let share = deliveryShare;
    if (channels && channels.length === 1) share = channels[0] === "DELIVERY" ? 1 : 0;
    share = Math.min(1, Math.max(0, Number(share) || 0));
    const average = (1 - share) * rate("STORE") + share * rate("DELIVERY");
    return {
      cost_rate: ingredientCost / listPrice,
      contribution_per_unit: listPrice - ingredientCost - listPrice * average,
    };
  }

  function planningAssumptions(observation, scenarioId) {
    const scenario = observation.planning_scenarios.find((row) => row.scenario_id === scenarioId);
    if (!scenario) throw new Error("알 수 없는 계획 시나리오입니다.");
    return { ...scenario.assumptions };
  }

  function parseRatePercent(text) {
    const trimmed = String(text ?? "").trim();
    if (!trimmed) return null;
    const value = Number(trimmed);
    if (!Number.isFinite(value) || value < 0 || value > 100) return null;
    return value / 100;
  }

  /** 고급 입력은 네 값이 모두 채워져야 계산한다. 빈 값을 기본값으로 채우지 않는다. */
  function readManualAssumptions(values) {
    const assumptions = {};
    for (const field of BUNDLE_RATE_FIELDS) {
      const parsed = parseRatePercent(values[field]);
      if (parsed === null) return null;
      assumptions[field] = parsed;
    }
    return assumptions;
  }

  function sourceLabel(source) {
    return SOURCE_LABELS[source] || "확인 필요";
  }

  function costSourceLabel(source) {
    return COST_SOURCE_LABELS[source] || "확인 필요";
  }

  function costBasisLabel(basis) {
    if (!basis) return "";
    const won = `${Math.round(basis.unit_cost).toLocaleString("ko-KR")}원`;
    return `식재료 원가 ${won} (${costSourceLabel(basis.source)})`;
  }

  function effectGroup(field) {
    return EFFECT_GROUPS[field] || "확인할 수 없는 값";
  }

  /** 위치 기능은 다음 단계에서 연결한다. 지금은 상태 구조와 안내 문구만 둔다. */
  function createLocationState() {
    return {
      status: "NOT_CONFIGURED",
      method: null,
      store: null,
      methods: {
        current_location: { available: false },
        store_search: { available: false },
      },
    };
  }

  function describeLocation(state) {
    if (state.status === "CONFIGURED" && state.store) return `${state.store.name} 위치 사용 중`;
    return "위치 기능 준비 중입니다.";
  }

  return {
    ANALYSIS_STATUS,
    BUNDLE_RATE_FIELDS,
    CATEGORY_LABELS,
    CHANNEL_LABELS,
    MAX_SCENARIOS,
    PLAN_DISCLAIMER,
    RATE_LABELS,
    analysisInfo,
    buildBundleRequest,
    buildPriceRequest,
    bundleMainMenus,
    bundleMenus,
    costBasisLabel,
    costSourceLabel,
    createLocationState,
    decisionGuidance,
    describeLocation,
    effectGroup,
    evidenceLabel,
    executionDefaults,
    fieldVisibility,
    isDebugMode,
    isPriceAnalyzable,
    parseRatePercent,
    plainText,
    planningAssumptions,
    priceMenus,
    priceScenarios,
    readManualAssumptions,
    scenarioName,
    sourceLabel,
    unitEconomics,
  };
});
