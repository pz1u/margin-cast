"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  ERROR_MESSAGE,
  REJECTED_MESSAGE,
  createChatController,
} = require("../web/assets/chat-core.js");

function completed(sessionId = "session-1") {
  return {
    session_id: sessionId,
    status: "COMPLETED",
    execution_id: "execution-1",
    recommendation_id: "recommendation-1",
    facts: {
      engine_decision: { value: "EXPERIMENT", source: "ENGINE" },
      profit_delta: { value: 135000, source: "ENGINE" },
      success_probability: { value: 0.82, source: "ENGINE" },
      forecast_used: { value: true, source: "ENGINE" },
      forecast_applied_dates: {
        value: { from: "2026-09-21", to: "2026-09-24" },
        source: "ENGINE",
      },
    },
    presentation: {
      explanation: "설명에 999라는 다른 값이 있어도 카드 값으로 사용하지 않습니다.",
      next_action: "작게 검증해보세요.",
      source: "POLICY_FALLBACK",
    },
    notices: ["합성 데이터 기반 프로토타입입니다."],
  };
}

test("메시지는 Agent endpoint로 전달되고 같은 대화의 session_id를 유지한다", async () => {
  const calls = [];
  const responses = [
    {
      session_id: "server-session",
      status: "NEEDS_INPUT",
      execution_id: "execution-1",
      question: "변경할 가격은 얼마로 생각하고 계신가요?",
    },
    completed("server-session"),
  ];
  const controller = createChatController({
    request: async (body) => {
      calls.push(body);
      return responses.shift();
    },
  });

  await controller.send("치킨마요 가격 올리면 어때?");
  await controller.send("9500원");

  assert.deepEqual(calls, [
    { message: "치킨마요 가격 올리면 어때?" },
    { session_id: "server-session", message: "9500원" },
  ]);
  const state = controller.getState();
  assert.equal(state.sessionId, "server-session");
  assert.equal(state.messages[1].kind, "question");
  assert.equal(state.messages[1].text, "변경할 가격은 얼마로 생각하고 계신가요?");
  assert.equal(state.messages[3].kind, "completed");
});

test("COMPLETED는 facts와 presentation을 분리하고 notices를 보존한다", async () => {
  const response = completed();
  const controller = createChatController({ request: async () => response });

  await controller.send("치킨마요를 9,500원으로 올리면 어때?");

  const message = controller.getState().messages[1];
  assert.deepEqual(message.facts, response.facts);
  assert.equal(message.facts.profit_delta.value, 135000);
  assert.deepEqual(message.facts.forecast_applied_dates.value, {
    from: "2026-09-21",
    to: "2026-09-24",
  });
  assert.equal(message.explanation, response.presentation.explanation);
  assert.equal(message.presentationSource, "POLICY_FALLBACK");
  assert.notEqual(message.explanation, String(message.facts.profit_delta.value));
  assert.deepEqual(message.notices, response.notices);
  assert.equal(message.recommendationId, "recommendation-1");
});

test("위치 선택은 같은 session으로 전달하고 정확한 위치를 화면 상태에 보존하지 않는다", async () => {
  const calls = [];
  const controller = createChatController({
    request: async (body) => {
      calls.push(body);
      if (calls.length === 1) {
        return {
          session_id: "weather-session",
          status: "NEEDS_INPUT",
          execution_id: "execution-location",
          question: "날씨를 확인할 매장 위치를 선택해주세요.",
          missing_input: {
            input_type: "location",
            options: [
              { value: "current_location", label: "현재 위치 사용" },
              { value: "address_search", label: "매장 위치 검색" },
            ],
          },
        };
      }
      return completed("weather-session");
    },
  });

  await controller.send("우리 매장 기준 다음 4일 동안 치킨마요 9,500원은 어때?");
  await controller.sendLocation(
    { source: "browser_geolocation", latitude: 37.5665, longitude: 126.978 },
    "현재 위치를 사용합니다.",
  );

  assert.equal(calls[1].session_id, "weather-session");
  assert.equal(calls[1].location.source, "browser_geolocation");
  assert.equal(calls[1].location.latitude, 37.5665);
  const stateText = JSON.stringify(controller.getState());
  assert.doesNotMatch(stateText, /37\.5665|126\.978|latitude|longitude/);
  assert.equal(controller.getState().messages[1].inputType, "location");
});

test("REJECTED는 내부 위반을 숨기고 사용자가 눌렀을 때만 재시도한다", async () => {
  const calls = [];
  const controller = createChatController({
    request: async (body) => {
      calls.push(body);
      if (calls.length === 1) {
        return {
          session_id: "session-retry",
          status: "REJECTED",
          execution_id: "execution-rejected",
          policy_validation: { violation_codes: ["INTERNAL_POLICY_CODE"] },
        };
      }
      return completed("session-retry");
    },
  });

  await controller.send("치킨마요를 9,500원으로 올리면 어때?");
  let state = controller.getState();
  assert.equal(calls.length, 1);
  assert.equal(state.messages[1].text, REJECTED_MESSAGE);
  assert.doesNotMatch(JSON.stringify(state), /INTERNAL_POLICY_CODE/);

  await controller.retry();
  state = controller.getState();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].session_id, "session-retry");
  assert.equal(state.messages.at(-1).kind, "completed");
});

test("ERROR는 서버 내부 메시지나 stack trace를 화면 상태에 보존하지 않는다", async () => {
  const controller = createChatController({
    request: async () => ({
      session_id: "session-error",
      status: "ERROR",
      execution_id: "execution-error",
      error: { message: "stack trace api_key=secret" },
    }),
  });

  await controller.send("가격을 봐줘");

  const stateText = JSON.stringify(controller.getState());
  assert.match(stateText, new RegExp(ERROR_MESSAGE));
  assert.doesNotMatch(stateText, /stack|api_key|secret/);
});

test("loading 중에는 중복 전송을 차단한다", async () => {
  let resolveRequest;
  let callCount = 0;
  const pending = new Promise((resolve) => { resolveRequest = resolve; });
  const controller = createChatController({
    request: async () => {
      callCount += 1;
      return pending;
    },
  });

  const first = controller.send("치킨마요를 9,500원으로 올리면 어때?");
  const second = await controller.send("중복 요청");

  assert.equal(controller.getState().isLoading, true);
  assert.equal(second, false);
  assert.equal(callCount, 1);
  resolveRequest(completed());
  await first;
  assert.equal(controller.getState().isLoading, false);
});

test("새 대화는 session과 화면 메시지를 초기화한다", async () => {
  const calls = [];
  const controller = createChatController({
    request: async (body) => {
      calls.push(body);
      return completed(`session-${calls.length}`);
    },
  });

  await controller.send("첫 질문");
  assert.equal(controller.getState().sessionId, "session-1");
  assert.equal(controller.newConversation(), true);
  assert.deepEqual(controller.getState().messages, []);
  assert.equal(controller.getState().sessionId, null);

  await controller.send("새 질문");
  assert.deepEqual(calls[1], { message: "새 질문" });
});
