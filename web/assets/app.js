"use strict";

const state = {
  capabilities: null,
  scenarioSequence: 0,
  latestPriceFeedback: null,
  pendingPlans: [],
};
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

function byId(id) { return document.getElementById(id); }
function won(value, signed = false) {
  const numeric = Number(value || 0);
  const prefix = signed && numeric > 0 ? "+" : "";
  return `${prefix}${number.format(Math.round(numeric))}원`;
}
function percent(value) { return `${(Number(value || 0) * 100).toFixed(1)}%`; }
function localIsoDate(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
function addDateDays(isoDate, days) {
  const value = new Date(`${isoDate}T12:00:00`);
  value.setDate(value.getDate() + days);
  return localIsoDate(value);
}
function predictionInterval(value) {
  return { mean: value.mean, p10: value.p10, p90: value.p90 };
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok || payload.status === "error") {
    throw new Error(payload.error?.message || "계산 요청을 처리하지 못했습니다.");
  }
  return payload;
}

function setServiceStatus(online, message) {
  byId("service-status").textContent = message;
  byId("status-dot").className = `status-dot ${online ? "is-online" : "is-offline"}`;
}

function updateDataStrip(capabilities) {
  const data = capabilities.data;
  byId("data-days").textContent = `${data.end_day_index - data.start_day_index + 1}일`;
  byId("data-rows").textContent = number.format(data.rows);
  byId("menu-count").textContent = `${capabilities.supported_menus.length}개`;
}

function currentMenu() {
  return state.capabilities?.supported_menus.find((menu) => menu.menu_id === byId("menu-select").value);
}

function addScenario(values = {}) {
  if (byId("scenario-list").children.length >= 8) return;
  state.scenarioSequence += 1;
  const menu = currentMenu();
  const baseline = menu?.baseline_price || 9000;
  const fragment = byId("scenario-template").content.cloneNode(true);
  const row = fragment.querySelector(".scenario-row");
  row.querySelector(".scenario-name").value = values.name || `${state.scenarioSequence * 500}원 인상`;
  row.querySelector(".scenario-price").value = values.list_price || baseline + state.scenarioSequence * 500;
  row.querySelector(".scenario-discount").value = values.discount || 0;
  row.querySelector(".remove-button").addEventListener("click", () => {
    if (byId("scenario-list").children.length > 1) row.remove();
  });
  byId("scenario-list").append(fragment);
}

function resetScenarios() {
  state.scenarioSequence = 0;
  byId("scenario-list").replaceChildren();
  addScenario();
  addScenario();
}

function scenarioPayload() {
  return [...document.querySelectorAll(".scenario-row")].map((row) => ({
    name: row.querySelector(".scenario-name").value.trim(),
    list_price: Number(row.querySelector(".scenario-price").value),
    discount: Number(row.querySelector(".scenario-discount").value),
  }));
}

function setDecision(element, action) {
  const labels = { RECOMMEND: "추천", EXPERIMENT: "소규모 실험", HOLD: "보류" };
  element.textContent = labels[action] || action;
  element.className = `decision-badge is-${String(action).toLowerCase()}`;
}

function addBarChart(container, strategies, recommendedName) {
  container.replaceChildren();
  const profits = strategies.map((strategy) => strategy.contribution_profit.mean);
  const maximum = Math.max(...profits, 1);
  strategies.forEach((strategy) => {
    const row = document.createElement("div");
    row.className = `bar-row${strategy.name === recommendedName ? " is-recommended" : ""}`;
    const label = document.createElement("span");
    label.className = "bar-label";
    label.textContent = strategy.name;
    label.title = strategy.name;
    const track = document.createElement("span");
    track.className = "bar-track";
    const fill = document.createElement("span");
    fill.className = "bar-fill";
    fill.style.width = `${Math.max(2, (strategy.contribution_profit.mean / maximum) * 100)}%`;
    track.append(fill);
    const value = document.createElement("span");
    value.className = "bar-value";
    value.textContent = won(strategy.contribution_profit.mean);
    row.append(label, track, value);
    container.append(row);
  });
}

function prepareFeedback(payload, strategy) {
  const horizonDays = payload.request.horizon_days;
  const startDate = payload.weather?.applied_from || localIsoDate();
  const endDate = payload.weather?.applied_to || addDateDays(startDate, horizonDays - 1);
  state.latestPriceFeedback = {
    menu_id: payload.request.menu_id,
    scenario: {
      name: strategy.name,
      list_price: strategy.list_price,
      discount: strategy.discount,
    },
    prediction: {
      horizon_days: horizonDays,
      units: predictionInterval(strategy.units),
      contribution_profit: predictionInterval(strategy.contribution_profit),
      profit_delta: predictionInterval(strategy.profit_delta),
    },
  };

  byId("feedback-no-prediction").hidden = true;
  byId("feedback-prediction").hidden = false;
  byId("feedback-strategy").textContent = strategy.name;
  byId("feedback-prediction-copy").textContent =
    `${horizonDays}일 · 예상 판매 ${number.format(Math.round(strategy.units.mean))}개 · ` +
    `예상 기여이익 ${won(strategy.contribution_profit.mean)}`;
  byId("feedback-name").value = `${strategy.name} ${horizonDays}일 실험`;
  byId("feedback-start").value = startDate;
  byId("feedback-end").value = endDate;
  [
    "feedback-name",
    "feedback-start",
    "feedback-end",
    "feedback-plan-submit",
  ].forEach((id) => { byId(id).disabled = false; });
  byId("feedback-plan-submit").firstElementChild.textContent = "실험 계획 저장하기";
  byId("feedback-plan-message").textContent = "";
  byId("feedback-plan-message").classList.remove("is-success");
}

function renderPendingPlan() {
  const selected = state.pendingPlans.find(
    (plan) => plan.feedback_id === byId("feedback-pending").value,
  );
  const inputIds = [
    "feedback-baseline-method",
    "feedback-units",
    "feedback-profit",
    "feedback-baseline-profit",
    "feedback-submit",
  ];
  inputIds.forEach((id) => { byId(id).disabled = !selected; });
  byId("feedback-pending-card").hidden = !selected;
  if (!selected) return;
  byId("feedback-pending-card").textContent =
    `${selected.scenario.name} · ${selected.start_date}—${selected.end_date} · ` +
    `예상 기여이익 ${won(selected.prediction.contribution_profit.mean)}`;
}

function renderPendingPlans(plans, selectedId = null) {
  state.pendingPlans = plans;
  const select = byId("feedback-pending");
  select.replaceChildren();
  if (!plans.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "저장된 계획 없음";
    select.append(option);
    select.disabled = true;
  } else {
    plans.forEach((plan) => {
      const option = document.createElement("option");
      option.value = plan.feedback_id;
      option.textContent = `${plan.experiment_name} · ${plan.end_date}`;
      select.append(option);
    });
    select.disabled = false;
    select.value = selectedId || plans[0].feedback_id;
  }
  renderPendingPlan();
}

function renderFeedbackSummary(summary, record = null) {
  const count = summary.record_count;
  byId("feedback-count").textContent = `${number.format(count)}건`;
  if (!summary.calibration) {
    byId("feedback-empty").hidden = false;
    byId("feedback-result").hidden = true;
    return;
  }

  const delta = summary.calibration.profit_delta;
  byId("feedback-empty").hidden = true;
  byId("feedback-result").hidden = false;
  byId("feedback-records").textContent = `${number.format(count)}건`;
  byId("feedback-delta-mae").textContent = won(delta.mean_absolute_error);
  byId("feedback-coverage").textContent = percent(delta.p80_coverage);
  byId("feedback-direction").textContent = percent(delta.direction_accuracy);
  if (record) {
    const evaluation = record.evaluation.profit_delta;
    byId("feedback-summary-title").textContent = "실험 결과가 기록됐습니다.";
    byId("feedback-last-result").textContent =
      `실제 이익 변화 ${won(evaluation.actual, true)}, 예측 오차 ${won(evaluation.error, true)}. ` +
      `실제 값은 80% 예측 범위${evaluation.within_80_interval ? " 안에 있습니다." : "를 벗어났습니다."}`;
  } else {
    byId("feedback-summary-title").textContent = "누적 보정 상태";
    byId("feedback-last-result").textContent =
      "저장된 실제 실험을 기준으로 예측 편향과 불확실성 범위를 평가합니다.";
  }
}

function renderPriceResult(payload) {
  const action = payload.recommended_action;
  const recommended = payload.strategies.find((strategy) => strategy.name === action.name);
  byId("price-empty").hidden = true;
  byId("price-result").hidden = false;
  setDecision(byId("price-decision"), action.action);
  byId("price-recommendation-name").textContent = action.name;
  byId("price-recommendation-reason").textContent = action.reason;
  byId("price-weather-context").textContent = payload.weather
    ? `실제 단기예보 · ${payload.weather.applied_from}—${payload.weather.applied_to}`
    : "최근 관측 날씨 문맥";
  byId("price-profit-delta").textContent = won(recommended.profit_delta.mean, true);
  byId("price-profit-range").textContent = `${won(recommended.profit_delta.p10, true)} — ${won(recommended.profit_delta.p90, true)}`;
  byId("price-success").textContent = percent(recommended.success_probability);
  byId("price-confidence").textContent = recommended.confidence?.label || "—";
  const confidence = recommended.confidence;
  if (confidence?.evidence) {
    const evidence = confidence.evidence;
    const promotion = evidence.uses_promotion_effect
      ? `, 할인 실험 ${evidence.promotion_events}회`
      : "";
    byId("price-evidence").textContent =
      `가격 실험 ${evidence.price_events}회${promotion}, 관측 결제가격 ` +
      `${won(evidence.observed_paid_price_range[0])}—${won(evidence.observed_paid_price_range[1])}. ` +
      confidence.interpretation;
  } else {
    byId("price-evidence").textContent = payload.interpretation_notes.join(" ");
  }
  addBarChart(byId("price-chart"), payload.strategies, action.name);
  prepareFeedback(payload, recommended);
}

function renderBundleResult(payload) {
  const strategy = payload.strategy;
  byId("bundle-empty").hidden = true;
  byId("bundle-result").hidden = false;
  setDecision(byId("bundle-decision"), payload.decision.action);
  byId("bundle-recommendation-name").textContent = strategy.name;
  byId("bundle-recommendation-reason").textContent = payload.decision.reason;
  byId("bundle-profit-delta").textContent = won(strategy.profit_delta.mean, true);
  byId("bundle-profit-range").textContent = `${won(strategy.profit_delta.p10, true)} — ${won(strategy.profit_delta.p90, true)}`;
  byId("bundle-success").textContent = percent(strategy.success_probability);
  byId("bundle-confidence").textContent = strategy.confidence.label;
  byId("bundle-evidence").textContent = strategy.confidence.reason;

  const orderLabels = {
    converted_main_without_drink: "단품 고객 전환",
    converted_existing_copurchase: "기존 동시구매 전환",
    incremental: "신규 주문",
    cannibalized_other_main: "다른 메뉴 잠식",
  };
  const container = byId("bundle-orders");
  container.replaceChildren();
  Object.entries(orderLabels).forEach(([key, label]) => {
    const item = document.createElement("div");
    item.className = "flow-item";
    const name = document.createElement("span");
    name.textContent = label;
    const value = document.createElement("strong");
    value.textContent = `${number.format(strategy.orders[key].mean)}건`;
    item.append(name, value);
    container.append(item);
  });
}

async function initialize() {
  try {
    const capabilities = await requestJson("/api/capabilities");
    state.capabilities = capabilities;
    const select = byId("menu-select");
    capabilities.supported_menus.forEach((menu) => {
      const option = document.createElement("option");
      option.value = menu.menu_id;
      option.textContent = `${menu.menu_name} · 현재 ${won(menu.baseline_price)}`;
      select.append(option);
    });
    updateDataStrip(capabilities);
    resetScenarios();
    setServiceStatus(true, "계산 엔진 연결됨");
    try {
      const [summary, pending] = await Promise.all([
        requestJson("/api/experiments/feedback/summary"),
        requestJson("/api/experiments/plans"),
      ]);
      renderFeedbackSummary(summary);
      renderPendingPlans(pending.plans);
    } catch (error) {
      byId("feedback-message").textContent = error.message;
    }
  } catch (error) {
    setServiceStatus(false, "계산 엔진 연결 실패");
    byId("price-message").textContent = error.message;
  }
}

document.querySelectorAll(".mode-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".mode-tab").forEach((item) => {
      const active = item === tab;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
      byId(item.dataset.panel).hidden = !active;
    });
  });
});

byId("add-scenario").addEventListener("click", () => addScenario());
byId("menu-select").addEventListener("change", resetScenarios);
byId("use-forecast").addEventListener("change", (event) => {
  const enabled = event.target.checked;
  const addressField = byId("forecast-address-field");
  const address = byId("store-address");
  const horizon = byId("price-horizon");
  addressField.hidden = !enabled;
  address.required = enabled;
  if (enabled) {
    horizon.dataset.previousValue = horizon.value;
    horizon.value = "4";
    horizon.disabled = true;
    address.focus();
  } else {
    horizon.disabled = false;
    horizon.value = horizon.dataset.previousValue || "14";
  }
});
byId("feedback-pending").addEventListener("change", renderPendingPlan);

byId("price-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("price-submit");
  const message = byId("price-message");
  button.disabled = true;
  message.textContent = "";
  button.firstElementChild.textContent = "계산 중…";
  try {
    const useForecast = byId("use-forecast").checked;
    const request = {
      menu_id: byId("menu-select").value,
      scenarios: scenarioPayload(),
      horizon_days: Number(byId("price-horizon").value),
      simulations: Number(byId("price-simulations").value),
      seed: Number(byId("price-seed").value),
    };
    if (useForecast) request.address = byId("store-address").value.trim();
    const endpoint = useForecast
      ? "/api/strategies/price/forecast"
      : "/api/strategies/price";
    const payload = await requestJson(endpoint, {
      method: "POST",
      body: JSON.stringify(request),
    });
    renderPriceResult(payload);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.firstElementChild.textContent = "전략 비교하기";
  }
});

byId("bundle-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("bundle-submit");
  const message = byId("bundle-message");
  button.disabled = true;
  message.textContent = "";
  button.firstElementChild.textContent = "계산 중…";
  try {
    const rate = (id) => Number(byId(id).value) / 100;
    const payload = await requestJson("/api/strategies/bundle", {
      method: "POST",
      body: JSON.stringify({
        scenario: {
          name: byId("bundle-name").value.trim(),
          bundle_price: Number(byId("bundle-price").value),
          take_rate: rate("take-rate"),
          copurchase_take_rate: rate("copurchase-rate"),
          incremental_demand_rate: rate("incremental-rate"),
          cannibalization_rate: rate("cannibalization-rate"),
        },
        horizon_days: Number(byId("bundle-horizon").value),
        simulations: 5000,
        seed: 42,
      }),
    });
    renderBundleResult(payload);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.firstElementChild.textContent = "세트 효과 계산하기";
  }
});

byId("feedback-plan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("feedback-plan-submit");
  const message = byId("feedback-plan-message");
  if (!state.latestPriceFeedback) {
    message.textContent = "먼저 가격·할인 전략을 계산하세요.";
    return;
  }

  button.disabled = true;
  message.textContent = "";
  message.classList.remove("is-success");
  button.firstElementChild.textContent = "저장 중…";
  let saved = false;
  try {
    const payload = await requestJson("/api/experiments/plans", {
      method: "POST",
      body: JSON.stringify({
        experiment_name: byId("feedback-name").value.trim(),
        menu_id: state.latestPriceFeedback.menu_id,
        start_date: byId("feedback-start").value,
        end_date: byId("feedback-end").value,
        scenario: state.latestPriceFeedback.scenario,
        prediction: state.latestPriceFeedback.prediction,
      }),
    });
    saved = true;
    message.textContent = `계획 저장 완료 · ${payload.plan.feedback_id.slice(0, 8)}`;
    message.classList.add("is-success");
    renderPendingPlans([...state.pendingPlans, payload.plan], payload.plan.feedback_id);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = saved;
    button.firstElementChild.textContent = saved ? "계획 저장 완료" : "실험 계획 저장하기";
  }
});

byId("feedback-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("feedback-submit");
  const message = byId("feedback-message");
  const feedbackId = byId("feedback-pending").value;
  if (!feedbackId) {
    message.textContent = "완료할 실험 계획을 선택하세요.";
    return;
  }

  button.disabled = true;
  message.textContent = "";
  message.classList.remove("is-success");
  button.firstElementChild.textContent = "기록 중…";
  let saved = false;
  try {
    const payload = await requestJson("/api/experiments/feedback", {
      method: "POST",
      body: JSON.stringify({
        feedback_id: feedbackId,
        baseline_method: byId("feedback-baseline-method").value,
        actual: {
          units: Number(byId("feedback-units").value),
          contribution_profit: Number(byId("feedback-profit").value),
          baseline_contribution_profit: Number(byId("feedback-baseline-profit").value),
        },
      }),
    });
    saved = true;
    message.textContent = `실제 결과 기록 완료 · ${payload.record.feedback_id.slice(0, 8)}`;
    message.classList.add("is-success");
    byId("feedback-units").value = "";
    byId("feedback-profit").value = "";
    byId("feedback-baseline-profit").value = "";
    renderPendingPlans(
      state.pendingPlans.filter((plan) => plan.feedback_id !== feedbackId),
    );
    renderFeedbackSummary(payload.summary, payload.record);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    if (!saved) button.disabled = false;
    button.firstElementChild.textContent = "실제 결과 기록하기";
  }
});

initialize();
