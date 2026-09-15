# MarginCast 에이전트 도구 연동 계약

이 문서는 AI 에이전트 본체를 만들기 직전의 계산 도구 경계를 정의한다. 에이전트의 모델,
시스템 프롬프트, 대화 흐름과 도구 등록 코드는 사용자가 직접 설계한다.

## 준비된 도구

### `get_margincast_capabilities`

계산 가능한 메뉴와 입력 한도, 데이터 범위, 해석 제약을 반환한다. 에이전트는 지원 정보가
필요하지만 유효한 세션 캐시가 없거나 지원 여부가 불명확할 때 조회한다. 매 사용자 요청마다
호출하지 않는다. 캐시 갱신 규칙은 [Agent 설계 계약](agent-contract.md)을 따른다.
v2에서 가격 실험 근거가 있는 메뉴는
`M01`, `M02`, `M03`이다.

### `compare_price_strategies`

가격·할인 대안을 현재 가격과 비교한다. 핵심 출력은 다음과 같다.

- 전략별 기대 판매량과 5·10·50·90·95 백분위
- 전략별 기대 기여이익과 5·10·50·90·95 백분위
- 현재 가격 대비 기대 기여이익 차이
- 현재 가격보다 기여이익이 높을 확률
- 10백분위 손실 여부인 `downside_risk`
- 성공확률과 분리된 근거 품질 `confidence`
- `RECOMMEND`·`EXPERIMENT`·`HOLD` 실행 판단
- 기대 기여이익이 가장 높은 대안

`highest_expected_profit`은 기대값만 본 별도 지표다. 최종 순서는 기대이익·개선확률·80%
하한·신뢰도를 함께 반영한 `decision_ranking`을 사용한다.

### `simulate_bundle_strategy`

치킨마요·콜라 세트 가격과 다음 네 비율을 명시적으로 받아 계산한다.

- 콜라가 없던 치킨마요 주문의 `take_rate`
- 기존 치킨마요+콜라 주문의 `copurchase_take_rate`
- 기존 치킨마요 수요 대비 `incremental_demand_rate`
- 다른 주메뉴의 `cannibalization_rate`

이 비율은 POS에서 직접 식별할 수 없으므로 에이전트가 확정값처럼 만들면 안 된다. 사용자 가정
또는 실제 실험으로 얻은 값을 넣고, 근거가 가정뿐이면 Decision Engine은 `EXPERIMENT`로 제한한다.

## 도구 등록에 사용할 코드

- 함수 스키마: `src.agent_tool_contracts.TOOL_SCHEMAS`
- 호출 디스패처: `src.agent_tool_contracts.execute_tool`
- 계산 서비스: `src.decision_service.MarginCastDecisionService`

에이전트 런타임에서 함수 호출을 받으면 `execute_tool(tool_name, arguments)`에 전달하고,
반환된 객체를 JSON 도구 응답으로 다시 넣으면 된다. 실제 도구 등록과 호출 루프는 아직
구현하지 않았다.

## 로컬 사전 검증

에이전트 없이 동일한 경계를 CLI로 확인할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m src.agent_tool_contracts get_margincast_capabilities
.\.venv\Scripts\python.exe -m src.agent_tool_contracts compare_price_strategies `
  --arguments-file examples\compare-price-strategies.json
.\.venv\Scripts\python.exe -m src.agent_tool_contracts simulate_bundle_strategy `
  --arguments-file examples\simulate-bundle-strategy.json
```

같은 입력과 seed는 같은 결과를 반환한다. 잘못된 입력은 프로세스 예외 대신 다음 형식의
오류 객체로 반환된다.

```json
{
  "status": "error",
  "error": {
    "code": "UNSUPPORTED_MENU",
    "message": "...",
    "retryable": false,
    "details": {
      "supported_menu_ids": ["M01"]
    }
  }
}
```

## 에이전트가 지켜야 할 해석 경계

1. 계산값을 LLM이 다시 만들거나 임의로 수정하지 않는다.
2. `ground_truth_used`가 `false`인지 확인한다.
3. 기대값과 함께 범위·성공확률·하방 위험·신뢰도·실행 판단을 보여준다.
4. 미래 날씨 예보가 없다는 가정을 알린다.
5. 지원되지 않는 메뉴에는 가격탄력성을 추측하지 않는다.
6. 세트 전략의 신규 수요와 잠식은 사용자 가정임을 분명히 표시한다.

## 사용자에게 남은 구현

1. Agent Schema와 Provider Interface 정의 (첫 공급자는 Ollama, 모델은 환경변수)
2. Mock LLM으로 시스템 프롬프트와 질문 흐름 검증
3. `TOOL_SCHEMAS` 등록
4. 함수 호출을 `execute_tool`로 전달
5. 도구 결과를 사용자가 이해할 수 있는 경영 언어로 설명

설계는 [Agent 설계 계약](agent-contract.md)에 정리했다. 함수 계약과 계산 서비스는
준비되어 있으며 Agent Schema, Provider와 호출 루프는 아직 구현하지 않았다.
