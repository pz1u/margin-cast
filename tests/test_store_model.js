"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const Model = require("../web/assets/store-model.js");

const webRoot = path.join(__dirname, "..", "web");
const read = (file) => fs.readFileSync(path.join(webRoot, file), "utf8");

const fees = {
  STORE: { platform: 0, payment: 0.02 },
  DELIVERY: { platform: 0.15, payment: 0.03 },
};
const menu = (overrides) => ({
  menu_id: "A1",
  menu_name: "메뉴",
  category: "MAIN",
  source: "POS",
  analysis: { status: "ANALYZABLE" },
  ...overrides,
});
const defaults = { horizon_days: 14, simulations: 10000, seed: 42 };

test("debug mode is enabled only by debug=1", () => {
  assert.equal(Model.isDebugMode("?debug=1"), true);
  assert.equal(Model.isDebugMode("?debug=0"), false);
  assert.equal(Model.isDebugMode(""), false);
  assert.equal(Model.isDebugMode(undefined), false);
});

test("normal users see no execution or model fields", () => {
  assert.deepEqual(Model.fieldVisibility(false), {
    simulations: false,
    seed: false,
    menuId: false,
    modelParameters: false,
  });
  assert.deepEqual(Object.values(Model.fieldVisibility(true)), [true, true, true, true]);
});

test("only engine-analyzable POS menus can be price targets", () => {
  const menus = [
    menu({ menu_id: "P1" }),
    menu({ menu_id: "P2", analysis: { status: "INSUFFICIENT_PRICE_VARIATION" } }),
    menu({ menu_id: "U001", source: "MANUAL", analysis: { status: "DATA_COLLECTING" } }),
  ];
  assert.deepEqual(Model.priceMenus(menus).map((row) => row.menu_id), ["P1"]);
  assert.equal(Model.isPriceAnalyzable(menus[2]), false);
  assert.equal(Model.isPriceAnalyzable(undefined), false);
});

test("a new menu is never presented as analyzable", () => {
  const info = Model.analysisInfo("DATA_COLLECTING");
  assert.equal(info.label, "데이터 수집 중");
  assert.notEqual(info.tone, "ok");
  assert.equal(Model.analysisInfo("ANALYZABLE").label, "분석 가능");
  assert.equal(
    Model.analysisInfo("INSUFFICIENT_PRICE_VARIATION").label,
    "가격 변화 데이터 부족",
  );
});

test("bundle menus come from POS menus and mains are MAIN only", () => {
  const menus = [
    menu({ menu_id: "M1" }),
    menu({ menu_id: "S1", category: "SIDE" }),
    menu({ menu_id: "U001", source: "MANUAL", analysis: { status: "DATA_COLLECTING" } }),
  ];
  assert.deepEqual(Model.bundleMenus(menus).map((row) => row.menu_id), ["M1", "S1"]);
  assert.deepEqual(Model.bundleMainMenus(menus).map((row) => row.menu_id), ["M1"]);
});

test("unit economics recalculates the cost rate and fees like the engine", () => {
  const input = { listPrice: 10000, ingredientCost: 4000, deliveryShare: 0.5 };
  const store = Model.unitEconomics(fees, { ...input, channels: ["STORE"] });
  assert.equal(store.cost_rate, 0.4);
  assert.equal(store.contribution_per_unit, 5800);
  const delivery = Model.unitEconomics(fees, { ...input, channels: ["DELIVERY"] });
  assert.equal(delivery.contribution_per_unit, 4200);
  const mixed = Model.unitEconomics(fees, { ...input, channels: ["STORE", "DELIVERY"] });
  assert.equal(mixed.contribution_per_unit, 5000);
  const higher = Model.unitEconomics(fees, { ...input, ingredientCost: 5000, channels: ["STORE"] });
  assert.equal(higher.cost_rate, 0.5);
  assert.equal(Model.unitEconomics(fees, { ...input, listPrice: 0 }), null);
});

test("price requests use engine defaults and never invent execution values", () => {
  const request = Model.buildPriceRequest({
    menuId: "P1",
    rows: [{ list_price: 9500, discount: 0 }, { list_price: 9000, discount: 1000 }],
    baselinePrice: 9000,
    defaults,
    horizonDays: 7,
    overrides: {},
  });
  assert.equal(request.simulations, 10000);
  assert.equal(request.seed, 42);
  assert.equal(request.horizon_days, 7);
  assert.deepEqual(request.scenarios.map((row) => row.name), ["500원 인상", "1,000원 할인"]);
  assert.throws(() =>
    Model.buildPriceRequest({ menuId: "P1", rows: [], baselinePrice: 9000, defaults: null, horizonDays: 7 }),
  );
});

test("debug overrides replace defaults only when they are integers", () => {
  const settings = Model.buildPriceRequest({
    menuId: "P1",
    rows: [{ list_price: 9500, discount: 0 }],
    baselinePrice: 9000,
    defaults,
    horizonDays: 14,
    overrides: { simulations: 500, seed: undefined },
  });
  assert.equal(settings.simulations, 500);
  assert.equal(settings.seed, 42);
});

test("duplicate scenario names are numbered", () => {
  const names = Model.priceScenarios(
    [{ list_price: 9500, discount: 0 }, { list_price: 9500, discount: 0 }],
    9000,
  ).map((row) => row.name);
  assert.deepEqual(names, ["500원 인상", "500원 인상 (2)"]);
});

test("bundle requests carry selected menus and the given assumptions only", () => {
  const assumptions = {
    take_rate: 0.1,
    copurchase_take_rate: 0.2,
    incremental_demand_rate: 0.3,
    cannibalization_rate: 0.4,
  };
  const request = Model.buildBundleRequest({
    mainMenuId: "P2",
    componentMenuIds: ["S1", "D1"],
    name: "세트",
    bundlePrice: 12000,
    assumptions,
    defaults,
    horizonDays: 14,
  });
  assert.equal(request.main_menu_id, "P2");
  assert.deepEqual(request.component_menu_ids, ["S1", "D1"]);
  assert.deepEqual(request.scenario, { name: "세트", bundle_price: 12000, ...assumptions });
  assert.equal(request.simulations, 10000);
});

test("manual bundle assumptions require all four values in range", () => {
  const values = {
    take_rate: "30",
    copurchase_take_rate: "55",
    incremental_demand_rate: "10",
    cannibalization_rate: "2",
  };
  assert.deepEqual(Model.readManualAssumptions(values), {
    take_rate: 0.3,
    copurchase_take_rate: 0.55,
    incremental_demand_rate: 0.1,
    cannibalization_rate: 0.02,
  });
  assert.equal(Model.readManualAssumptions({ ...values, take_rate: "" }), null);
  assert.equal(Model.readManualAssumptions({ ...values, cannibalization_rate: "101" }), null);
  assert.equal(Model.readManualAssumptions({ ...values, take_rate: "-1" }), null);
  assert.equal(Model.parseRatePercent("abc"), null);
});

test("planning assumptions come from the engine observation", () => {
  const observation = {
    planning_scenarios: [
      { scenario_id: "base", assumptions: { take_rate: 0.3 }, source: "DEFAULT" },
    ],
  };
  assert.deepEqual(Model.planningAssumptions(observation, "base"), { take_rate: 0.3 });
  assert.throws(() => Model.planningAssumptions(observation, "unknown"));
  assert.equal(Model.sourceLabel("DEFAULT"), "계획용 기본값");
  assert.equal(Model.sourceLabel("USER"), "직접 입력");
});

test("cost basis shows whether the cost is user edited", () => {
  assert.match(Model.costBasisLabel({ unit_cost: 5900, source: "USER" }), /직접 수정한 값/);
  assert.match(Model.costBasisLabel({ unit_cost: 4900, source: "POS_HISTORY" }), /POS 원가 이력/);
});

test("location features are prepared but not connected", () => {
  const state = Model.createLocationState();
  assert.equal(state.status, "NOT_CONFIGURED");
  assert.equal(state.methods.current_location.available, false);
  assert.equal(state.methods.store_search.available, false);
  assert.match(Model.describeLocation(state), /준비 중/);
});

test("default screen keeps expert inputs behind debug or advanced sections", () => {
  const html = read("index.html");
  const debugBlocks = [...html.matchAll(/<div class="debug-box" data-debug-only>[\s\S]*?<\/div>\s*<\/div>/g)];
  const debugText = debugBlocks.map((match) => match[0]).join("\n");
  ["price-simulations", "price-seed", "bundle-simulations", "bundle-seed"].forEach((id) => {
    assert.ok(debugText.includes(`id="${id}"`), `${id} must live in a debug-only block`);
  });
  const withoutDebug = html.replace(/<div class="debug-box" data-debug-only>[\s\S]*?<\/div>\s*<\/div>/g, "");
  assert.doesNotMatch(withoutDebug, /price-simulations|price-seed|bundle-simulations|bundle-seed/);

  const advanced = html.match(/<details class="advanced-options" id="bundle-advanced">[\s\S]*?<\/details>/);
  assert.ok(advanced, "advanced bundle inputs must be in a collapsed details block");
  assert.doesNotMatch(advanced[0].split(">")[0], /\bopen\b/);
  Model.BUNDLE_RATE_FIELDS.forEach((field) => {
    assert.ok(advanced[0].includes(`data-rate="${field}"`));
    assert.equal(html.split(`data-rate="${field}"`).length - 1, 1);
  });
  assert.doesNotMatch(withoutDebug, /elasticity|탄력성|\bnx\b|\bny\b/i);
  assert.match(html, /<th scope="col" data-debug-only>내부 ID<\/th>/);
});

test("no menu ids or bundle pair are hard coded in the store screens", () => {
  ["index.html", "assets/store-ui.js", "assets/store-model.js"].forEach((file) => {
    assert.doesNotMatch(read(file), /\bM0\d\b/, `${file} must not hard code menu ids`);
  });
  const ui = read("assets/store-ui.js");
  assert.doesNotMatch(ui, /simulations:\s*\d+|seed:\s*\d+/);
});

test("fees are shown read only and no fee input exists", () => {
  const html = read("index.html");
  assert.match(html, /현재 엔진 적용값 · 읽기 전용/);
  const feeSection = html.match(/<section class="block" aria-labelledby="fee-title">[\s\S]*?<\/section>/)[0];
  assert.doesNotMatch(feeSection, /<input|<select|<textarea/);
});

test("agent chat panel and its wiring are preserved", () => {
  const html = read("index.html");
  ["chat-messages", "chat-composer", "chat-input", "send-button", "new-chat"].forEach((id) => {
    assert.ok(html.includes(`id="${id}"`), `${id} must remain`);
  });
  assert.match(html, /chat-core\.js/);
  assert.match(read("assets/app.js"), /\/api\/agent\/chat/);
  assert.match(html, /id="agent-tab"[^>]*>AI 상담</);
});
