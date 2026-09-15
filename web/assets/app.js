"use strict";

const state = { capabilities: null, scenarioSequence: 0 };
const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

function byId(id) { return document.getElementById(id); }
function won(value, signed = false) {
  const numeric = Number(value || 0);
  const prefix = signed && numeric > 0 ? "+" : "";
  return `${prefix}${number.format(Math.round(numeric))}원`;
}
function percent(value) { return `${(Number(value || 0) * 100).toFixed(1)}%`; }

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

initialize();
