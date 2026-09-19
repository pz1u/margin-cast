(function initializeMarginCastChat(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MarginCastChatCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createApi() {
  "use strict";

  const STATUS = Object.freeze({
    COMPLETED: "COMPLETED",
    NEEDS_INPUT: "NEEDS_INPUT",
    REJECTED: "REJECTED",
    ERROR: "ERROR",
  });

  const SAFE_COPY = (value) => JSON.parse(JSON.stringify(value));
  const REJECTED_MESSAGE = "응답을 안전하게 생성하지 못했습니다. 다시 시도해주세요.";
  const ERROR_MESSAGE = "일시적으로 분석을 완료하지 못했습니다. 잠시 후 다시 시도해주세요.";

  function createChatController({ request }) {
    if (typeof request !== "function") throw new TypeError("request 함수가 필요합니다.");

    const listeners = new Set();
    const state = {
      sessionId: null,
      messages: [],
      isLoading: false,
      lastRequestMessage: null,
    };

    function snapshot() {
      return SAFE_COPY(state);
    }

    function notify() {
      const current = snapshot();
      listeners.forEach((listener) => listener(current));
    }

    function appendAssistant(payload) {
      const base = {
        id: `assistant-${state.messages.length + 1}`,
        role: "assistant",
        status: payload.status,
        executionId: payload.execution_id || null,
      };

      if (payload.status === STATUS.COMPLETED) {
        state.messages.push({
          ...base,
          kind: "completed",
          explanation: String(payload.presentation?.explanation || ""),
          nextAction: String(payload.presentation?.next_action || ""),
          presentationSource: String(payload.presentation?.source || "LLM"),
          facts: SAFE_COPY(payload.facts || {}),
          notices: Array.isArray(payload.notices) ? payload.notices.map(String) : [],
          recommendationId: payload.recommendation_id || null,
        });
        return;
      }

      if (payload.status === STATUS.NEEDS_INPUT) {
        state.messages.push({
          ...base,
          kind: "question",
          text: String(payload.question || "필요한 정보를 알려주세요."),
        });
        return;
      }

      if (payload.status === STATUS.REJECTED) {
        state.messages.push({ ...base, kind: "rejected", text: REJECTED_MESSAGE });
        return;
      }

      state.messages.push({ ...base, kind: "error", text: ERROR_MESSAGE });
    }

    async function send(message, options = {}) {
      const text = typeof message === "string" ? message.trim() : "";
      if (!text || state.isLoading) return false;

      const appendUser = options.appendUser !== false;
      if (appendUser) {
        state.messages.push({
          id: `user-${state.messages.length + 1}`,
          role: "user",
          kind: "text",
          text,
        });
      }
      state.lastRequestMessage = text;
      state.isLoading = true;
      notify();

      try {
        const requestPayload = { message: text };
        if (state.sessionId) requestPayload.session_id = state.sessionId;
        const payload = await request(requestPayload);
        if (payload && typeof payload.session_id === "string") {
          state.sessionId = payload.session_id;
        }
        appendAssistant(payload || { status: STATUS.ERROR });
      } catch (_error) {
        appendAssistant({ status: STATUS.ERROR });
      } finally {
        state.isLoading = false;
        notify();
      }
      return true;
    }

    async function retry() {
      if (state.isLoading || !state.lastRequestMessage) return false;
      const last = state.messages[state.messages.length - 1];
      if (last?.role === "assistant" && ["rejected", "error"].includes(last.kind)) {
        state.messages.pop();
      }
      return send(state.lastRequestMessage, { appendUser: false });
    }

    function newConversation() {
      if (state.isLoading) return false;
      state.sessionId = null;
      state.messages = [];
      state.lastRequestMessage = null;
      notify();
      return true;
    }

    function subscribe(listener) {
      if (typeof listener !== "function") throw new TypeError("listener가 필요합니다.");
      listeners.add(listener);
      listener(snapshot());
      return () => listeners.delete(listener);
    }

    return {
      send,
      retry,
      newConversation,
      subscribe,
      getState: snapshot,
    };
  }

  return {
    STATUS,
    REJECTED_MESSAGE,
    ERROR_MESSAGE,
    createChatController,
  };
});
