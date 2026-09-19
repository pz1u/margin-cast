"use strict";

(function initializeStoreUi() {
  const Model = window.MarginCastStoreModel;
  const debug = Model.isDebugMode(window.location.search);
  const visibility = Model.fieldVisibility(debug);
  document.body.dataset.debug = debug ? "1" : "0";

  const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const state = {
    capabilities: null,
    profile: null,
    location: Model.createLocationState(),
    scenarioSequence: 0,
    observation: null,
    observationToken: 0,
  };

  function byId(id) { return document.getElementById(id); }
  function make(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = text;
    return element;
  }
  function won(value, signed = false) {
    const numeric = Number(value || 0);
    const prefix = signed && numeric > 0 ? "+" : "";
    return `${prefix}${number.format(Math.round(numeric))}원`;
  }
  function percent(value, digits = 1) { return `${(Number(value || 0) * 100).toFixed(digits)}%`; }

  async function requestJson(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => null);
    if (!payload || !response.ok || payload.status === "error") {
      throw new Error(payload?.error?.message || "요청을 처리하지 못했습니다.");
    }
    return payload;
  }
  function post(path, body) {
    return requestJson(path, { method: "POST", body: JSON.stringify(body) });
  }

  function menus() { return state.profile?.menus || []; }
  function findMenu(menuId) { return menus().find((menu) => menu.menu_id === menuId); }
  function executionDefaults(tool) {
    return Model.executionDefaults(state.capabilities, tool);
  }

  // ------------------------------------------------------------------ tabs
  const tabs = [...document.querySelectorAll(".app-tab")];
  function activateTab(name, { focus = false, updateHash = true } = {}) {
    const target = tabs.find((tab) => tab.dataset.panel === name) || tabs[0];
    tabs.forEach((tab) => {
      const active = tab === target;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      byId(`${tab.dataset.panel}-panel`).hidden = !active;
    });
    byId("new-chat").hidden = target.dataset.panel !== "agent";
    if (focus) target.focus();
    if (updateHash) window.history.replaceState(null, "", `${window.location.search}#${target.dataset.panel}`);
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.panel));
    tab.addEventListener("keydown", (event) => {
      const last = tabs.length - 1;
      const next = {
        ArrowRight: tabs[index === last ? 0 : index + 1],
        ArrowLeft: tabs[index === 0 ? last : index - 1],
        Home: tabs[0],
        End: tabs[last],
      }[event.key];
      if (!next) return;
      event.preventDefault();
      activateTab(next.dataset.panel, { focus: true });
    });
  });
  document.querySelectorAll("[data-goto]").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.goto));
  });

  // ------------------------------------------------------------- dashboard
  function renderDashboard() {
    const store = state.profile.store;
    const demo = !store.uses_actual_store_data;
    byId("st-connection").textContent = store.connected ? "연동됨" : "연동 안 됨";
    byId("st-connection-note").textContent = demo ? "데모: 실제 POS 대신 합성 데이터를 사용 중" : "";
    byId("pos-tag").textContent = demo ? "데모 데이터" : "실제 POS";
    byId("st-synced").textContent = store.last_synced_at
      ? new Date(store.last_synced_at).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" })
      : "-";
    byId("st-synced-note").textContent = demo ? "데모 데이터를 불러온 시각" : "";
    byId("st-orders").textContent = `${number.format(store.order_count)}건`;
    byId("st-orders-note").textContent = store.last_order_at
      ? `마지막 주문 ${new Date(store.last_order_at).toLocaleDateString("ko-KR", { dateStyle: "medium" })}`
      : "";
    byId("st-menus").textContent = `${store.menu_count}개`;
    byId("st-menus-note").textContent = store.custom_menu_count
      ? `POS 메뉴 ${store.menu_count - store.custom_menu_count}개 + 직접 추가 ${store.custom_menu_count}개`
      : "POS에서 가져온 메뉴";
    byId("st-analyzable").textContent = `${store.analyzable_menu_count}개`;
    byId("st-analyzable-note").textContent =
      `전체 ${store.menu_count}개 중 가격 시뮬레이션을 쓸 수 있는 메뉴`;
    byId("st-dataset").textContent = demo ? "합성 데이터" : "POS";
    byId("st-dataset-note").textContent = store.dataset_version;
    byId("demo-notice").querySelector("span").textContent = store.demo_notice;

    const rows = byId("fee-rows");
    rows.replaceChildren();
    const fees = state.profile.fees;
    [["STORE", "매장"], ["DELIVERY", "배달"]].forEach(([channel, label]) => {
      const row = make("tr");
      row.append(make("th", "", label));
      row.lastChild.scope = "row";
      row.append(make("td", "num", percent(fees[channel].platform, 0)));
      row.append(make("td", "num", percent(fees[channel].payment, 0)));
      rows.append(row);
    });
    byId("location-status").textContent = Model.describeLocation(state.location);
  }

  // ----------------------------------------------------------------- menus
  function economicsCells(row, economics) {
    row.querySelector(".cell-rate").textContent = percent(economics.cost_rate);
    row.querySelector(".cell-contribution").textContent = won(economics.contribution_per_unit);
  }

  function sourceBadge(menu) {
    return menu.source === "MANUAL" ? "직접 입력한 원가" : Model.costSourceLabel(menu.cost_source);
  }

  function labeled(cell, label) {
    cell.dataset.label = label;
    return cell;
  }

  function renderMenuRow(menu) {
    const row = make("tr");
    row.dataset.menuId = menu.menu_id;
    const debugCell = make("td", "debug-cell", menu.menu_id);
    debugCell.dataset.debugOnly = "";
    labeled(debugCell, "내부 ID");
    row.append(debugCell);

    const nameCell = make("td", "menu-name");
    nameCell.append(make("strong", "", menu.menu_name));
    const channels = menu.channels.map((channel) => Model.CHANNEL_LABELS[channel]).join("·");
    nameCell.append(
      make(
        "small",
        "",
        `${Model.CATEGORY_LABELS[menu.category]} · ${channels} · ${menu.source === "POS" ? "POS 메뉴" : "직접 추가"}`,
      ),
    );
    row.append(nameCell);

    row.append(labeled(make("td", "num", won(menu.list_price)), "판매가"));

    const costCell = labeled(make("td", "num cost-cell"), "식재료 원가");
    const input = make("input", "cost-input");
    input.type = "number";
    input.min = "0";
    input.max = "100000";
    input.step = "10";
    input.value = String(menu.ingredient_cost);
    input.setAttribute("aria-label", `${menu.menu_name} 식재료 원가`);
    const save = make("button", "mini-button", "저장");
    save.type = "button";
    save.disabled = true;
    save.setAttribute("aria-label", `${menu.menu_name} 원가 저장`);
    costCell.append(input, save);
    if (menu.source === "POS" && menu.cost_source === "USER") {
      const restore = make(
        "button",
        "text-button restore-button",
        `POS 기준값으로 되돌리기 (${won(menu.pos_ingredient_cost)})`,
      );
      restore.type = "button";
      restore.addEventListener("click", () => saveCost(menu.menu_id, { reset: true }, restore));
      costCell.append(restore);
    }
    costCell.append(make("small", `cost-source is-${menu.cost_source.toLowerCase()}`, sourceBadge(menu)));
    row.append(costCell);

    row.append(labeled(make("td", "num cell-rate"), "원가율"));
    row.append(labeled(make("td", "num cell-contribution"), "단품 기여이익"));
    economicsCells(row, menu.economics);

    const analysis = Model.analysisInfo(menu.analysis.status);
    const statusCell = labeled(make("td", "analysis-cell"), "분석 상태");
    statusCell.append(make("span", `chip is-${analysis.tone}`, analysis.label));
    statusCell.append(make("small", "", analysis.message));
    if (menu.analysis.status === "ANALYZABLE" && menu.cost_source === "USER") {
      statusCell.append(make("small", "engine-note", "수정한 원가가 기여이익 시뮬레이션에 반영됩니다."));
    }
    const actions = make("div", "row-actions");
    if (Model.isPriceAnalyzable(menu)) {
      const go = make("button", "text-button", "전략 보기 →");
      go.type = "button";
      go.addEventListener("click", () => {
        activateTab("strategy");
        showStrategy("price");
        byId("price-menu").value = menu.menu_id;
        onPriceMenuChange();
      });
      actions.append(go);
    }
    if (menu.source === "MANUAL") {
      const remove = make("button", "text-button danger", "삭제");
      remove.type = "button";
      remove.addEventListener("click", () => deleteMenu(menu, remove));
      actions.append(remove);
    }
    statusCell.append(actions);
    row.append(statusCell);

    input.addEventListener("input", () => {
      const cost = Number(input.value);
      const valid = input.value !== "" && Number.isInteger(cost) && cost >= 0 && cost <= 100000;
      save.disabled = !valid || cost === menu.ingredient_cost;
      if (!valid) return;
      economicsCells(
        row,
        Model.unitEconomics(state.profile.fees, {
          listPrice: menu.list_price,
          ingredientCost: cost,
          channels: menu.channels,
          deliveryShare: menu.economics.delivery_share,
        }),
      );
    });
    const commit = () => {
      if (!save.disabled) saveCost(menu.menu_id, { ingredient_cost: Number(input.value) }, save);
    };
    save.addEventListener("click", commit);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        commit();
      }
    });
    return row;
  }

  function renderMenus() {
    const body = byId("menu-rows");
    body.replaceChildren(...menus().map(renderMenuRow));
    const custom = menus().filter((menu) => menu.source === "MANUAL").length;
    byId("menu-summary").textContent =
      `메뉴 ${menus().length}개 · 분석 가능 ${state.profile.store.analyzable_menu_count}개` +
      (custom ? ` · 직접 추가 ${custom}개` : "");
  }

  async function refreshProfile() {
    state.profile = await requestJson("/api/store/profile");
    renderDashboard();
    renderMenus();
    renderPriceMenus();
    renderBundleMenus();
  }

  async function saveCost(menuId, body, button) {
    const message = byId("menu-message");
    message.textContent = "";
    message.classList.remove("is-success");
    button.disabled = true;
    try {
      await post("/api/store/menus/cost", { menu_id: menuId, ...body });
      await refreshProfile();
      markStale("price");
      markStale("bundle");
      byId("menu-message").textContent = "원가를 저장했습니다. 다음 시뮬레이션부터 반영됩니다.";
      byId("menu-message").classList.add("is-success");
    } catch (error) {
      message.textContent = error.message;
      button.disabled = false;
    }
  }

  async function deleteMenu(menu, button) {
    byId("menu-message").classList.remove("is-success");
    button.disabled = true;
    try {
      await post("/api/store/menus/delete", { menu_id: menu.menu_id });
      await refreshProfile();
      byId("menu-message").textContent = `${menu.menu_name} 메뉴를 삭제했습니다.`;
      byId("menu-message").classList.add("is-success");
    } catch (error) {
      byId("menu-message").textContent = error.message;
      button.disabled = false;
    }
  }

  // ------------------------------------------------------------- add menu
  const addForm = byId("add-menu-form");
  function selectedChannels() {
    return [...addForm.querySelectorAll('input[name="new-channel"]:checked')].map((box) => box.value);
  }
  function renderAddPreview() {
    const price = Number(byId("new-price").value);
    const cost = Number(byId("new-cost").value);
    const filled = byId("new-price").value !== "" && byId("new-cost").value !== "";
    const share = state.profile?.store.delivery_share ?? 0.5;
    const result = filled && state.profile
      ? Model.unitEconomics(state.profile.fees, {
        listPrice: price,
        ingredientCost: cost,
        channels: selectedChannels(),
        deliveryShare: share,
      })
      : null;
    byId("new-cost-rate").textContent = result ? percent(result.cost_rate) : "-";
    byId("new-contribution").textContent = result ? won(result.contribution_per_unit) : "-";
  }
  function setAddFormOpen(open) {
    addForm.hidden = !open;
    byId("add-menu-toggle").setAttribute("aria-expanded", String(open));
    if (open) byId("new-name").focus();
  }
  byId("add-menu-toggle").addEventListener("click", () => setAddFormOpen(addForm.hidden));
  byId("add-menu-cancel").addEventListener("click", () => setAddFormOpen(false));
  addForm.addEventListener("input", renderAddPreview);
  addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = byId("add-menu-message");
    message.textContent = "";
    const channels = selectedChannels();
    if (!channels.length) {
      message.textContent = "판매 채널을 하나 이상 선택하세요.";
      return;
    }
    const button = byId("add-menu-submit");
    button.disabled = true;
    try {
      const added = await post("/api/store/menus", {
        menu_name: byId("new-name").value.trim(),
        category: byId("new-category").value,
        list_price: Number(byId("new-price").value),
        ingredient_cost: Number(byId("new-cost").value),
        channels,
      });
      addForm.reset();
      renderAddPreview();
      setAddFormOpen(false);
      await refreshProfile();
      byId("menu-message").textContent =
        "메뉴를 추가했습니다. 판매·가격 변경 데이터가 쌓이기 전까지 가격 시뮬레이션은 사용할 수 없습니다.";
      byId("menu-message").classList.add("is-success");
      const row = byId("menu-rows").querySelector(`tr[data-menu-id="${added.menu.menu_id}"]`);
      if (row) {
        row.classList.add("is-new");
        row.scrollIntoView({ block: "center" });
      }
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  // -------------------------------------------------------------- strategy
  const staleTargets = {
    price: { result: "price-result", note: "price-stale" },
    bundle: { result: "bundle-result", note: "bundle-stale" },
  };
  function markStale(kind) {
    const target = staleTargets[kind];
    if (byId(target.result).hidden) return;
    byId(target.result).classList.add("is-stale");
    byId(target.note).hidden = false;
  }
  function clearStale(kind) {
    const target = staleTargets[kind];
    byId(target.result).classList.remove("is-stale");
    byId(target.note).hidden = true;
  }
  function scrollToResult(id) {
    if (window.matchMedia("(max-width: 900px)").matches) byId(id).scrollIntoView({ block: "start" });
  }

  function showStrategy(kind) {
    document.querySelectorAll(".subtab").forEach((tab) => {
      const active = tab.dataset.strategy === kind;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    byId("price-strategy").hidden = kind !== "price";
    byId("bundle-strategy").hidden = kind !== "bundle";
  }
  const subtabs = [...document.querySelectorAll(".subtab")];
  subtabs.forEach((tab, index) => {
    tab.addEventListener("click", () => showStrategy(tab.dataset.strategy));
    tab.addEventListener("keydown", (event) => {
      const next = { ArrowRight: subtabs[(index + 1) % subtabs.length], ArrowLeft: subtabs[(index + subtabs.length - 1) % subtabs.length] }[event.key];
      if (!next) return;
      event.preventDefault();
      showStrategy(next.dataset.strategy);
      next.focus();
    });
  });

  function setDecision(element, action) {
    const labels = { RECOMMEND: "추천", EXPERIMENT: "소규모 실험", HOLD: "보류" };
    element.textContent = labels[action] || action;
    element.className = `decision-badge is-${String(action).toLowerCase()}`;
  }

  function evidenceText(quality) {
    return Model.evidenceLabel(quality);
  }

  // price ---------------------------------------------------------------
  function selectedPriceMenu() { return findMenu(byId("price-menu").value); }

  function renderPriceMenus() {
    const select = byId("price-menu");
    const previous = select.value;
    const options = Model.priceMenus(menus());
    select.replaceChildren(
      ...options.map((menu) => {
        const option = make("option", "", `${menu.menu_name} · 현재 ${won(menu.list_price)}`);
        option.value = menu.menu_id;
        return option;
      }),
    );
    if (options.some((menu) => menu.menu_id === previous)) select.value = previous;
    const unavailable = menus().length - options.length;
    byId("price-menu-note").textContent = options.length
      ? `가격 변화 이력이 있는 ${options.length}개 메뉴만 분석할 수 있습니다.` +
        (unavailable ? ` 나머지 ${unavailable}개는 메뉴 관리에서 분석 상태를 확인하세요.` : "")
      : "지금 가격 시뮬레이션을 사용할 수 있는 메뉴가 없습니다.";
    byId("price-submit").disabled = !options.length;
    renderPriceCostLine();
  }

  function renderPriceCostLine() {
    const menu = selectedPriceMenu();
    byId("price-cost-line").textContent = menu
      ? Model.costBasisLabel({ unit_cost: menu.ingredient_cost, source: menu.cost_source })
      : "";
  }

  function syncScenarioButtons() {
    const rows = document.querySelectorAll(".scenario-row");
    rows.forEach((row) => { row.querySelector(".remove-button").disabled = rows.length <= 1; });
    byId("add-scenario").disabled = rows.length >= Model.MAX_SCENARIOS;
  }

  function addScenario(values = {}) {
    if (byId("scenario-list").children.length >= Model.MAX_SCENARIOS) return;
    state.scenarioSequence += 1;
    const baseline = selectedPriceMenu()?.list_price || 9000;
    const fragment = byId("scenario-template").content.cloneNode(true);
    const row = fragment.querySelector(".scenario-row");
    row.querySelector(".scenario-price").value = values.list_price || baseline + state.scenarioSequence * 500;
    row.querySelector(".scenario-discount").value = values.discount || 0;
    row.querySelector(".remove-button").addEventListener("click", () => {
      if (byId("scenario-list").children.length > 1) {
        row.remove();
        syncScenarioButtons();
        markStale("price");
      }
    });
    byId("scenario-list").append(fragment);
    syncScenarioButtons();
  }

  function resetScenarios() {
    state.scenarioSequence = 0;
    byId("scenario-list").replaceChildren();
    addScenario();
    addScenario();
  }

  function onPriceMenuChange() {
    resetScenarios();
    renderPriceCostLine();
    markStale("price");
  }

  function scenarioRows() {
    return [...document.querySelectorAll(".scenario-row")].map((row) => ({
      list_price: Number(row.querySelector(".scenario-price").value),
      discount: Number(row.querySelector(".scenario-discount").value),
    }));
  }

  function debugOverrides(prefix) {
    if (!debug) return {};
    const read = (id) => (byId(id).value === "" ? undefined : Number(byId(id).value));
    return { simulations: read(`${prefix}-simulations`), seed: read(`${prefix}-seed`) };
  }

  function addBarChart(container, strategies, recommendedName) {
    container.replaceChildren();
    const maximum = Math.max(...strategies.map((s) => s.contribution_profit.mean), 1);
    strategies.forEach((strategy) => {
      const row = make("div", `bar-row${strategy.name === recommendedName ? " is-recommended" : ""}`);
      const label = make("span", "bar-label", strategy.name);
      label.title = strategy.name;
      const track = make("span", "bar-track");
      const fill = make("span", "bar-fill");
      fill.style.width = `${Math.max(2, (strategy.contribution_profit.mean / maximum) * 100)}%`;
      track.append(fill);
      row.append(label, track, make("span", "bar-value", won(strategy.contribution_profit.mean)));
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
    byId("price-guidance").textContent = Model.decisionGuidance(action.action);
    byId("price-recommendation-reason").textContent = Model.plainText(action.reason);
    byId("price-profit-delta").textContent = won(recommended.profit_delta.mean, true);
    byId("price-profit-range").textContent =
      `${won(recommended.profit_delta.p10, true)} ~ ${won(recommended.profit_delta.p90, true)}`;
    byId("price-success").textContent = percent(recommended.success_probability);
    byId("price-confidence").textContent = evidenceText(recommended.evidence_quality);
    const quality = recommended.evidence_quality;
    byId("price-evidence").textContent = quality?.evidence
      ? `가격 실험 ${quality.evidence.price_events}회, 관측 결제가격 ` +
        `${won(quality.evidence.observed_paid_price_range[0])} ~ ${won(quality.evidence.observed_paid_price_range[1])}. ` +
        Model.plainText(`${quality.interpretation} ${quality.validation.statement}`)
      : Model.plainText(payload.interpretation_notes.join(" "));
    byId("price-basis").textContent =
      `${Model.costBasisLabel(payload.cost_basis)}. 원가를 고쳐도 예상 판매량은 달라지지 않습니다. ` +
      `${payload.data_provenance.warning}`;
    byId("price-debug").textContent = JSON.stringify(
      { model: payload.model, cost_basis: payload.cost_basis, request: payload.request },
      null,
      2,
    );
    addBarChart(byId("price-chart"), payload.strategies, action.name);
  }

  byId("price-menu").addEventListener("change", onPriceMenuChange);
  byId("add-scenario").addEventListener("click", () => { addScenario(); markStale("price"); });
  byId("price-form").addEventListener("input", () => markStale("price"));
  byId("price-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("price-submit");
    const message = byId("price-message");
    const menu = selectedPriceMenu();
    message.textContent = "";
    if (!menu) return;
    button.disabled = true;
    button.textContent = "계산 중…";
    try {
      const request = Model.buildPriceRequest({
        menuId: menu.menu_id,
        rows: scenarioRows(),
        baselinePrice: menu.list_price,
        defaults: executionDefaults("compare_price_strategies"),
        horizonDays: Number(byId("price-horizon").value),
        overrides: debugOverrides("price"),
      });
      renderPriceResult(await post("/api/strategies/price", request));
      clearStale("price");
      scrollToResult("price-result-title");
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = "전략 비교하기";
    }
  });

  // bundle --------------------------------------------------------------
  function renderBundleMenus() {
    const mains = Model.bundleMainMenus(menus());
    const select = byId("bundle-main");
    const previous = select.value;
    select.replaceChildren(
      ...mains.map((menu) => {
        const option = make("option", "", `${menu.menu_name} · ${won(menu.list_price)}`);
        option.value = menu.menu_id;
        return option;
      }),
    );
    if (mains.some((menu) => menu.menu_id === previous)) select.value = previous;
    renderComponentChoices();
  }

  function selectedComponents() {
    return [...byId("bundle-components").querySelectorAll("input:checked")].map((box) => box.value);
  }

  function renderComponentChoices() {
    const container = byId("bundle-components");
    const checked = new Set(selectedComponents());
    const mainId = byId("bundle-main").value;
    container.replaceChildren(
      ...Model.bundleMenus(menus())
        .filter((menu) => menu.menu_id !== mainId)
        .map((menu) => {
          const label = make("label", "check");
          const box = make("input");
          box.type = "checkbox";
          box.value = menu.menu_id;
          box.checked = checked.has(menu.menu_id);
          label.append(box, make("span", "", `${menu.menu_name} · ${won(menu.list_price)}`));
          return label;
        }),
    );
    loadObservation();
  }

  function setBundleActions(enabled) {
    byId("bundle-plan-submit").disabled = !enabled;
    byId("bundle-manual-submit").disabled = !enabled;
  }

  async function loadObservation() {
    const token = ++state.observationToken;
    const components = selectedComponents();
    const observed = byId("bundle-observed");
    const unknown = byId("bundle-unknown");
    const message = byId("bundle-message");
    message.textContent = "";
    state.observation = null;
    setBundleActions(false);
    byId("bundle-plan-values").hidden = true;
    observed.replaceChildren();
    unknown.replaceChildren();
    byId("bundle-price-note").textContent = "";
    if (!byId("bundle-main").value || components.length === 0) {
      observed.append(make("p", "hint", "주메뉴와 구성 메뉴를 고르면 POS에서 확인된 값을 보여드립니다."));
      return;
    }
    if (components.length > 4) {
      message.textContent = "구성 메뉴는 최대 4개까지 선택할 수 있습니다.";
      return;
    }
    try {
      const observation = await post("/api/store/bundle/observation", {
        main_menu_id: byId("bundle-main").value,
        component_menu_ids: components,
      });
      if (token !== state.observationToken) return;
      state.observation = observation;
      renderObservation(observation);
    } catch (error) {
      if (token === state.observationToken) message.textContent = error.message;
    }
  }

  function renderObservation(observation) {
    const observed = byId("bundle-observed");
    if (!observation.ready) {
      observed.append(make("p", "hint", observation.not_ready_reason));
      return;
    }
    const facts = observation.observed;
    [
      ["관측 기간", `${facts.period_days}일`],
      ["주메뉴 판매량", `${number.format(facts.main_units_sold)}개`],
      ["주메뉴 주문", `${number.format(facts.main_orders)}건`],
      ["함께 구매된 횟수", `${number.format(facts.copurchase_orders)}건`],
      ["기존 동시구매율 (주문 중 함께 산 비율)", percent(facts.copurchase_rate)],
    ].forEach(([label, value]) => {
      const item = make("div");
      item.append(make("dt", "", label), make("dd", "", value));
      observed.append(item);
    });
    observation.unidentifiable.forEach((item) => {
      const line = make("li");
      line.append(
        make("strong", "", Model.effectGroup(item.field)),
        make("span", "", Model.RATE_LABELS[item.field] || item.label),
        make("small", "", item.note),
      );
      byId("bundle-unknown").append(line);
    });
    renderPlanValues(observation);
    byId("bundle-price-note").textContent = `구성 메뉴 정가 합계 ${won(observation.list_price_total)}`;
    setBundleActions(true);
  }

  function renderPlanValues(observation) {
    const rows = byId("bundle-plan-rows");
    rows.replaceChildren();
    observation.planning_scenarios.forEach((scenario) => {
      const tr = make("tr");
      const name = make("th", "", scenario.label);
      name.scope = "row";
      tr.append(name);
      Model.BUNDLE_RATE_FIELDS.forEach((field) => {
        tr.append(make("td", "num", percent(scenario.assumptions[field], 0)));
      });
      rows.append(tr);
    });
    byId("bundle-plan-disclaimer").textContent = Model.PLAN_DISCLAIMER;
    byId("bundle-plan-values").hidden = false;
  }

  function bundleName(label) {
    const observation = state.observation;
    const main = observation.main.menu_name;
    return `${main} 세트 · ${label}`.slice(0, 50);
  }

  async function runBundle(rows) {
    const message = byId("bundle-message");
    message.textContent = "";
    const price = Number(byId("bundle-price").value);
    if (!byId("bundle-price").value || !Number.isInteger(price)) {
      message.textContent = "세트 가격을 입력하세요.";
      return;
    }
    const buttons = [byId("bundle-plan-submit"), byId("bundle-manual-submit")];
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const results = [];
      for (const row of rows) {
        const request = Model.buildBundleRequest({
          mainMenuId: state.observation.main.menu_id,
          componentMenuIds: state.observation.components.map((menu) => menu.menu_id),
          name: bundleName(row.label),
          bundlePrice: price,
          assumptions: row.assumptions,
          defaults: executionDefaults("simulate_bundle_strategy"),
          horizonDays: Number(byId("bundle-horizon").value),
          overrides: debugOverrides("bundle"),
        });
        results.push({ row, payload: await post("/api/store/bundle/simulate", request) });
      }
      renderBundleResults(results);
      clearStale("bundle");
      scrollToResult("bundle-result-title");
    } catch (error) {
      message.textContent = error.message;
    } finally {
      setBundleActions(Boolean(state.observation?.ready));
    }
  }

  function renderBundleResults(results) {
    byId("bundle-empty").hidden = true;
    byId("bundle-result").hidden = false;
    const body = byId("bundle-rows");
    body.replaceChildren();
    results.forEach(({ row, payload }) => {
      const strategy = payload.strategy;
      const tr = make("tr");
      const name = make("th", "");
      name.scope = "row";
      name.append(make("strong", "", row.label), make("small", "source-tag", Model.sourceLabel(row.source)));
      tr.append(name);
      tr.append(labeled(make("td", "num", won(strategy.profit_delta.mean, true)), "기대 기여이익 변화"));
      tr.append(labeled(make("td", "num", `${won(strategy.profit_delta.p10, true)} ~ ${won(strategy.profit_delta.p90, true)}`), "80% 범위"));
      tr.append(labeled(make("td", "num", percent(strategy.success_probability)), "개선확률"));
      tr.append(labeled(make("td", "", evidenceText(strategy.evidence_quality)), "근거 품질"));
      const decisionCell = labeled(make("td"), "판단");
      const badge = make("span", "decision-badge");
      setDecision(badge, payload.decision.action);
      decisionCell.append(badge);
      tr.append(decisionCell);
      body.append(tr);
    });
    const first = results[0].payload;
    const usesPlan = results.some(({ row }) => row.source === "DEFAULT");
    byId("bundle-plan-banner").textContent = usesPlan
      ? `보수·기준·낙관 결과는 ${Model.PLAN_DISCLAIMER}`
      : "직접 입력한 가정으로 계산한 결과이며 실제 POS 데이터에서 추정된 값이 아닙니다.";
    byId("bundle-evidence").textContent =
      `${first.strategy.evidence_quality.reason} ${first.strategy.evidence_quality.validation.statement}`;
    const names = new Map(menus().map((menu) => [menu.menu_id, menu.menu_name]));
    const costs = first.cost_basis
      .map((item) => `${names.get(item.menu_id) || item.menu_id} ${won(item.unit_cost)}(${Model.costSourceLabel(item.source)})`)
      .join(", ");
    byId("bundle-basis").textContent =
      `식재료 원가 ${costs}. 세트 효과 비율은 관측값이 아닌 가정입니다. 원가를 고쳐도 예상 판매량은 달라지지 않습니다. ` +
      `${first.data_provenance.warning}`;
  }

  byId("bundle-main").addEventListener("change", () => { renderComponentChoices(); markStale("bundle"); });
  byId("bundle-components").addEventListener("change", () => { loadObservation(); markStale("bundle"); });
  byId("bundle-form").addEventListener("input", () => markStale("bundle"));
  byId("bundle-plan-submit").addEventListener("click", () => {
    if (!state.observation?.ready) return;
    runBundle(
      state.observation.planning_scenarios.map((scenario) => ({
        label: scenario.label,
        source: scenario.source,
        assumptions: Model.planningAssumptions(state.observation, scenario.scenario_id),
      })),
    );
  });
  byId("bundle-manual-submit").addEventListener("click", () => {
    if (!state.observation?.ready) return;
    const values = {};
    byId("bundle-rate-grid").querySelectorAll("input[data-rate]").forEach((input) => {
      values[input.dataset.rate] = input.value;
    });
    const assumptions = Model.readManualAssumptions(values);
    if (!assumptions) {
      byId("bundle-message").textContent = "네 값을 모두 0~100% 범위로 직접 입력하세요.";
      return;
    }
    runBundle([{ label: "직접 입력", source: "USER", assumptions }]);
  });

  // ------------------------------------------------------------------ init
  async function initialize() {
    try {
      const [capabilities, profile] = await Promise.all([
        requestJson("/api/capabilities"),
        requestJson("/api/store/profile"),
      ]);
      state.capabilities = capabilities;
      state.profile = profile;
      byId("service-status").textContent = "계산 엔진 연결됨";
      const horizon = executionDefaults("compare_price_strategies")?.horizon_days;
      if (horizon) {
        const select = byId("price-horizon");
        if (![...select.options].some((option) => option.value === String(horizon))) {
          const option = make("option", "", `${horizon}일`);
          option.value = String(horizon);
          select.append(option);
        }
        select.value = String(horizon);
      }
      const bundleHorizon = executionDefaults("simulate_bundle_strategy")?.horizon_days;
      if (bundleHorizon) byId("bundle-horizon").value = String(bundleHorizon);
      if (visibility.simulations || visibility.seed) {
        const price = executionDefaults("compare_price_strategies");
        const bundle = executionDefaults("simulate_bundle_strategy");
        byId("price-simulations").value = price.simulations;
        byId("price-seed").value = price.seed;
        byId("bundle-simulations").value = bundle.simulations;
        byId("bundle-seed").value = bundle.seed;
      }
      renderDashboard();
      renderMenus();
      renderPriceMenus();
      resetScenarios();
      renderBundleMenus();
    } catch (error) {
      byId("service-status").textContent = "계산 엔진 연결 실패";
      byId("demo-notice").querySelector("span").textContent = error.message;
    }
  }

  const initialTab = window.location.hash.replace("#", "");
  activateTab(tabs.some((tab) => tab.dataset.panel === initialTab) ? initialTab : "dashboard", {
    updateHash: false,
  });
  initialize();
})();
