(function initializeMarginCastStoreModel(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MarginCastStoreModel = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createApi() {
  "use strict";

  const ANALYSIS_STATUS = Object.freeze({
    ANALYZABLE: { label: "분석 가능", tone: "ok" },
    INSUFFICIENT_PRICE_VARIATION: { label: "가격 변화 데이터 부족", tone: "wait" },
    DATA_COLLECTING: { label: "데이터 수집 중", tone: "wait" },
  });
  const SOURCE_LABELS = Object.freeze({
    USER: "직접 입력",
    ENGINE: "엔진 값",
    DEFAULT: "계획용 기본값",
    POS_HISTORY: "POS 원가 이력",
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
    return ANALYSIS_STATUS[status] || { label: String(status || "확인 필요"), tone: "wait" };
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
    return SOURCE_LABELS[source] || String(source || "");
  }

  function costBasisLabel(basis) {
    if (!basis) return "";
    const won = `${Math.round(basis.unit_cost).toLocaleString("ko-KR")}원`;
    return basis.source === "USER"
      ? `식재료 원가 ${won} (직접 수정한 값)`
      : `식재료 원가 ${won} (POS 원가 이력)`;
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
    return "위치 기능 준비 중 · 현재는 최근 관측 날씨를 사용합니다.";
  }

  return {
    ANALYSIS_STATUS,
    BUNDLE_RATE_FIELDS,
    CATEGORY_LABELS,
    CHANNEL_LABELS,
    MAX_SCENARIOS,
    analysisInfo,
    buildBundleRequest,
    buildPriceRequest,
    bundleMainMenus,
    bundleMenus,
    costBasisLabel,
    createLocationState,
    describeLocation,
    executionDefaults,
    fieldVisibility,
    isDebugMode,
    isPriceAnalyzable,
    parseRatePercent,
    planningAssumptions,
    priceMenus,
    priceScenarios,
    readManualAssumptions,
    scenarioName,
    sourceLabel,
    unitEconomics,
  };
});
