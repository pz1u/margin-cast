"use strict";

const { createChatController } = window.MarginCastChatCore;
const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 });
const wonFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
const loadingCopies = [
  "매장 데이터를 확인하고 있어요",
  "전략을 계산하고 있어요",
  "결과를 정리하고 있어요",
];

const elements = {
  messages: document.getElementById("chat-messages"),
  form: document.getElementById("chat-composer"),
  input: document.getElementById("chat-input"),
  send: document.getElementById("send-button"),
  newChat: document.getElementById("new-chat"),
};

let loadingTimer = null;
let loadingPosition = 0;

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = text;
  return element;
}

function factValue(facts, name) {
  const fact = facts?.[name];
  return fact && Object.prototype.hasOwnProperty.call(fact, "value") ? fact.value : null;
}

function formatWon(value, signed = false) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  const prefix = signed && numeric > 0 ? "+" : "";
  return `${prefix}${wonFormat.format(Math.round(numeric))}원`;
}

function formatNumber(value, suffix = "") {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numberFormat.format(numeric)}${suffix}` : "—";
}

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  const percentage = Math.abs(numeric) <= 1 ? numeric * 100 : numeric;
  return `${numberFormat.format(percentage)}%`;
}

function decisionMeta(value) {
  return {
    RECOMMEND: { label: "추천", className: "is-recommend", index: "01" },
    EXPERIMENT: { label: "소규모 실험", className: "is-experiment", index: "02" },
    HOLD: { label: "보류", className: "is-hold", index: "03" },
  }[value] || { label: String(value || "판단 확인"), className: "is-neutral", index: "—" };
}

function evidenceLabel(value) {
  if (!value || typeof value !== "object") return String(value || "—");
  const labels = { HIGH: "높음", MEDIUM: "보통", LOW: "낮음" };
  const calibrated = value.validation?.empirically_calibrated;
  const suffix = calibrated === false ? " · 미보정" : calibrated === true ? " · 보정됨" : "";
  return `${labels[value.label] || value.label || "—"}${suffix}`;
}

function appendMetric(container, label, value, className = "") {
  const metric = createElement("div", `result-metric ${className}`.trim());
  metric.append(createElement("span", "metric-label", label));
  metric.append(createElement("strong", "metric-value", value));
  container.append(metric);
}

function formatDateRange(value) {
  if (!value || typeof value !== "object") return "적용 날짜 확인";
  const from = typeof value.from === "string" ? value.from.replaceAll("-", ".") : "—";
  const to = typeof value.to === "string" ? value.to.replaceAll("-", ".") : "—";
  return `${from} ~ ${to}`;
}

function renderResultCard(message) {
  const facts = message.facts || {};
  const decision = decisionMeta(factValue(facts, "engine_decision"));
  const interval = factValue(facts, "interval_80");
  const lower = interval && typeof interval === "object" ? interval.lower : null;
  const upper = interval && typeof interval === "object" ? interval.upper : null;
  const downsideRisk = factValue(facts, "downside_risk");

  const card = createElement("section", "decision-card");
  card.setAttribute("aria-label", "MarginCast 계산 결과");

  const header = createElement("div", "decision-header");
  const title = createElement("div", "decision-title");
  title.append(createElement("span", "decision-index", `판단 ${decision.index}`));
  title.append(createElement("h3", "", "실행 판단"));
  const badge = createElement("strong", `decision-badge ${decision.className}`, decision.label);
  header.append(title, badge);
  card.append(header);

  if (factValue(facts, "forecast_used") === true) {
    const weather = createElement("div", "weather-context");
    weather.append(createElement("span", "weather-context-label", "실제 단기예보 반영"));
    weather.append(
      createElement(
        "strong",
        "weather-context-dates",
        formatDateRange(factValue(facts, "forecast_applied_dates")),
      ),
    );
    card.append(weather);
  }

  const primary = createElement("div", "primary-result");
  primary.append(createElement("span", "metric-label", "기대 기여이익 변화"));
  primary.append(
    createElement(
      "strong",
      "primary-value",
      formatWon(factValue(facts, "profit_delta"), true),
    ),
  );
  primary.append(
    createElement(
      "p",
      "primary-context",
      `개선확률 ${formatPercent(factValue(facts, "success_probability"))}`,
    ),
  );
  card.append(primary);

  const metrics = createElement("div", "result-metrics");
  if (interval !== null) {
    appendMetric(metrics, "80% 예상 범위", `${formatWon(lower)} ~ ${formatWon(upper)}`);
  }
  if (factValue(facts, "downside_risk") !== null) {
    appendMetric(metrics, "하방 위험", downsideRisk ? "있음" : "낮음", downsideRisk ? "is-caution" : "");
  }
  if (factValue(facts, "evidence_quality") !== null) {
    appendMetric(metrics, "근거 품질", evidenceLabel(factValue(facts, "evidence_quality")));
  }
  if (factValue(facts, "expected_units") !== null) {
    appendMetric(metrics, "예상 판매량", formatNumber(factValue(facts, "expected_units"), "개"));
  }
  if (factValue(facts, "expected_contribution_profit") !== null) {
    appendMetric(metrics, "기대 기여이익", formatWon(factValue(facts, "expected_contribution_profit")));
  }
  card.append(metrics);

  if (message.notices.length) {
    const notices = createElement("aside", "result-notices");
    notices.append(createElement("strong", "", "데이터와 가정"));
    const list = createElement("ul");
    message.notices.forEach((notice) => list.append(createElement("li", "", notice)));
    notices.append(list);
    card.append(notices);
  }

  if (new URLSearchParams(window.location.search).get("debug") === "1") {
    const details = createElement("details", "developer-details");
    details.append(createElement("summary", "", "개발 정보"));
    const reference = createElement("dl");
    reference.append(createElement("dt", "", "execution_id"));
    reference.append(createElement("dd", "", message.executionId || "—"));
    reference.append(createElement("dt", "", "recommendation_id"));
    reference.append(createElement("dd", "", message.recommendationId || "—"));
    reference.append(createElement("dt", "", "presentation_source"));
    reference.append(createElement("dd", "", message.presentationSource || "—"));
    details.append(reference);
    card.append(details);
  }
  return card;
}

function renderLoading() {
  const loading = createElement("article", "message message-agent message-loading");
  loading.id = "agent-loading";
  const top = createElement("div", "loading-topline");
  top.append(createElement("span", "message-author", "MarginCast Agent"));
  const dots = createElement("span", "loading-dots");
  dots.setAttribute("aria-hidden", "true");
  dots.append(createElement("i"), createElement("i"), createElement("i"));
  top.append(dots);
  loading.append(top);
  loading.append(createElement("p", "loading-copy", loadingCopies[loadingPosition]));
  loading.append(createElement("small", "loading-note", "응답을 기다리는 동안 분석 절차를 안내해드려요."));
  return loading;
}

function renderLocationActions(article, message) {
  const options = new Set((message.options || []).map((option) => option.value));
  const panel = createElement("div", "location-actions");
  const status = createElement("p", "location-status");
  status.setAttribute("aria-live", "polite");

  const addressForm = createElement("form", "location-search-form");
  addressForm.hidden = true;
  const addressLabel = createElement("label", "visually-hidden", "매장 주소 검색");
  const addressInput = createElement("input", "location-search-input");
  addressInput.type = "search";
  addressInput.maxLength = 200;
  addressInput.placeholder = "도로명 또는 지번 주소";
  addressInput.autocomplete = "street-address";
  addressLabel.append(addressInput);
  const addressSubmit = createElement("button", "location-submit", "위치 확인");
  addressSubmit.type = "submit";
  addressForm.append(addressLabel, addressSubmit);

  if (options.has("current_location")) {
    const currentButton = createElement("button", "location-action-button", "현재 위치 사용");
    currentButton.type = "button";
    currentButton.addEventListener("click", () => {
      if (!navigator.geolocation) {
        status.textContent = "현재 위치를 사용할 수 없습니다. 매장 위치를 검색해주세요.";
        addressForm.hidden = false;
        addressInput.focus();
        return;
      }
      currentButton.disabled = true;
      status.textContent = "브라우저 위치 권한을 확인하고 있어요.";
      navigator.geolocation.getCurrentPosition(
        (position) => {
          controller.sendLocation(
            {
              source: "browser_geolocation",
              latitude: position.coords.latitude,
              longitude: position.coords.longitude,
            },
            "현재 위치를 사용합니다.",
          );
        },
        () => {
          currentButton.disabled = false;
          status.textContent = "위치 권한을 사용할 수 없습니다. 매장 위치를 검색해주세요.";
          addressForm.hidden = false;
          addressInput.focus();
        },
        { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
      );
    });
    panel.append(currentButton);
  }

  if (options.has("address_search")) {
    const searchButton = createElement("button", "location-action-button", "매장 위치 검색");
    searchButton.type = "button";
    searchButton.addEventListener("click", () => {
      addressForm.hidden = false;
      status.textContent = "주소는 위치 확인 뒤 서버에 보관하지 않습니다.";
      addressInput.focus();
    });
    panel.append(searchButton);
  }

  addressForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const addressQuery = addressInput.value.trim();
    if (!addressQuery) {
      status.textContent = "검색할 매장 주소를 입력해주세요.";
      addressInput.focus();
      return;
    }
    controller.sendLocation(
      { source: "address_search", address_query: addressQuery },
      "매장 위치를 검색합니다.",
    );
  });

  panel.append(addressForm, status);
  article.append(panel);
}

function renderMessage(message) {
  if (message.role === "user") {
    const article = createElement("article", "message message-user");
    article.append(createElement("span", "message-author", "나"));
    article.append(createElement("p", "", message.text));
    return article;
  }

  const article = createElement("article", `message message-agent message-${message.kind}`);
  article.append(createElement("span", "message-author", "MarginCast Agent"));

  if (message.kind === "completed") {
    const explanation = createElement("div", "agent-explanation");
    if (message.explanation) explanation.append(createElement("p", "", message.explanation));
    if (message.nextAction) {
      const next = createElement("p", "next-action");
      next.append(createElement("strong", "", "다음 행동"));
      next.append(document.createTextNode(` ${message.nextAction}`));
      explanation.append(next);
    }
    article.append(explanation, renderResultCard(message));
    return article;
  }

  article.append(createElement("p", "", message.text));
  if (message.kind === "question" && message.inputType === "location") {
    renderLocationActions(article, message);
  }
  if (message.kind === "rejected" || message.kind === "error") {
    const retry = createElement("button", "retry-button", "다시 시도");
    retry.type = "button";
    retry.addEventListener("click", () => controller.retry());
    article.append(retry);
  }
  return article;
}

function welcomeMessage() {
  const article = createElement("article", "message message-agent message-welcome");
  article.append(createElement("span", "message-author", "MarginCast Agent"));
  article.append(
    createElement(
      "p",
      "",
      "검토할 메뉴와 변경 가격을 알려주세요. 실제 날씨를 반영하려면 매장 위치를 확인할게요.",
    ),
  );
  return article;
}

function scrollToLatest() {
  window.requestAnimationFrame(() => {
    elements.messages.scrollTo({ top: elements.messages.scrollHeight, behavior: "smooth" });
  });
}

function updateLoadingTimer(isLoading) {
  if (!isLoading) {
    window.clearInterval(loadingTimer);
    loadingTimer = null;
    loadingPosition = 0;
    return;
  }
  if (loadingTimer) return;
  loadingTimer = window.setInterval(() => {
    loadingPosition = Math.min(loadingPosition + 1, loadingCopies.length - 1);
    const copy = document.querySelector("#agent-loading .loading-copy");
    if (copy) copy.textContent = loadingCopies[loadingPosition];
  }, 2800);
}

function render(state) {
  elements.messages.replaceChildren();
  elements.messages.append(welcomeMessage());
  state.messages.forEach((message) => elements.messages.append(renderMessage(message)));
  if (state.isLoading) elements.messages.append(renderLoading());
  elements.messages.setAttribute("aria-busy", String(state.isLoading));
  elements.input.disabled = state.isLoading;
  elements.send.disabled = state.isLoading || !elements.input.value.trim();
  elements.newChat.disabled = state.isLoading;
  updateLoadingTimer(state.isLoading);
  scrollToLatest();
}

async function requestAgent(body) {
  const response = await fetch("/api/agent/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  if (!payload || typeof payload.status !== "string") throw new Error("invalid response");
  return payload;
}

const controller = createChatController({ request: requestAgent });
controller.subscribe(render);

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.input.value;
  elements.input.value = "";
  elements.input.style.height = "auto";
  await controller.send(message);
});

elements.input.addEventListener("input", () => {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 132)}px`;
  elements.send.disabled = controller.getState().isLoading || !elements.input.value.trim();
});

elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});

elements.newChat.addEventListener("click", () => {
  if (controller.newConversation()) {
    elements.input.value = "";
    elements.input.style.height = "auto";
    elements.send.disabled = true;
    elements.input.focus();
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.input.value = button.dataset.prompt;
    elements.input.dispatchEvent(new Event("input"));
    elements.input.focus();
  });
});
