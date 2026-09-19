# MarginCast 에이전트 도구 연동 계약

이 문서는 AI 에이전트 본체를 만들기 직전의 계산 도구 경계를 정의한다. 에이전트의 모델,
시스템 프롬프트, 대화 흐름과 도구 등록 코드는 사용자가 직접 설계한다.

## 준비된 도구

### `get_margincast_capabilities`

계산 가능한 메뉴와 입력 한도, 데이터 출처, 데이터 범위, 해석 제약을 반환한다. 에이전트 초기화 때
조회해 캐시하고, 데이터 버전이 바뀌거나 지원 여부가 불명확할 때 다시 확인한다. 사용자 요청마다
반복 호출하지 않는다. v2에서 가격 실험 근거가 있는 메뉴는
`M01`, `M02`, `M03`이다.

#### 실행 기본값

capabilities 응답은 도구별 실행 기본값을 반환한다.

```json
{
  "execution_defaults": {
    "compare_price_strategies": {
      "horizon_days": 14,
      "simulations": 10000,
      "seed": 42
    },
    "compare_price_strategies_with_forecast": {
      "horizon_days": 4,
      "simulations": 10000,
      "seed": 42
    },
    "simulate_bundle_strategy": {
      "horizon_days": 14,
      "simulations": 10000,
      "seed": 42
    }
  }
}
```

이 값은 `src/execution_defaults.py`의 단일 상수에서 엔진 함수 기본값과 capabilities 응답을 함께
생성한다. Agent와 시스템 프롬프트는 값을 복제하지 않는다. 사용자가 실행값을 지정하지 않으면
해당 도구의 기본값을 `DEFAULT` 출처로 기록한다.

#### 정적 계약과 동적 데이터 상태

- 정적 계약: `engine_version`, 도구 이름·입출력 스키마, `execution_defaults`
- 동적 데이터 상태: `data_version`, 지원 메뉴, 분석 가능 기간, 메뉴별 데이터 충분성

`data_version`은 코드 계약이 아니므로 `static_capabilities`에 저장하지 않는다. capabilities의
`data_provenance`처럼 데이터 상태를 나타내는 별도 영역에서 관리하고, 데이터 버전이 바뀌거나
데이터 상태와 관련된 도구 실패가 발생하면 동적 상태 캐시를 다시 확인한다.

### `compare_price_strategies`

가격·할인 대안을 현재 가격과 비교한다. 핵심 출력은 다음과 같다.

- 전략별 기대 판매량과 5·10·50·90·95 백분위
- 전략별 기대 기여이익과 5·10·50·90·95 백분위
- 현재 가격 대비 기대 기여이익 차이
- 현재 가격보다 기여이익이 높을 확률
- 10백분위 손실 여부인 `downside_risk`
- 성공확률과 분리된 미보정 근거 품질 `evidence_quality`
- `RECOMMEND`·`EXPERIMENT`·`HOLD` 실행 판단
- 기대 기여이익이 가장 높은 대안
- 모든 전략과 `recommended_action`을 연결하는 ENGINE 생성 `scenario_id`

`highest_expected_profit`은 기대값만 본 별도 지표다. 최종 순서는 기대이익·개선확률·80%
하한·근거 품질을 함께 반영한 `decision_ranking`을 사용한다. Agent는 배열 위치·이름·가격으로
추천 전략을 찾지 않고 `recommended_action.scenario_id`와 같은 ID의 전략만 선택한다.
`scenario_id`는 ENGINE이 메뉴·정가·할인 입력으로 결정하며 표시 이름이나 배열 순서가 바뀌어도
같은 사업 조건에는 같은 ID를 사용한다.

모든 계산 결과는 `data_provenance`로 합성/실제 데이터 여부와 데이터 버전을 밝힌다. provenance는
항상 ENGINE fact로 추적한다. `label=SYNTHETIC_DATA_PROTOTYPE`일 때만 synthetic warning을
필수로 검사하며 실제 데이터 provenance에는 해당 경고를 요구하지 않는다. 현재는
`SYNTHETIC_DATA_PROTOTYPE`이며 실제 매장 성과를 보증하지 않는다. `evidence_quality`는
`heuristic-v1`이고 실제 매장 결과와의 관계가 아직 검증되지 않은 상태다. `version`과 함께
`formula_fingerprint`를 보존해 당시 가중치·임계값을 재현한다.

### `simulate_bundle_strategy`

치킨마요·콜라 세트 가격과 다음 네 비율을 명시적으로 받아 계산한다.

- 콜라가 없던 치킨마요 주문의 `take_rate`
- 기존 치킨마요+콜라 주문의 `copurchase_take_rate`
- 기존 치킨마요 수요 대비 `incremental_demand_rate`
- 다른 주메뉴의 `cannibalization_rate`

이 비율은 POS에서 직접 식별할 수 없으므로 에이전트가 확정값처럼 만들면 안 된다. 사용자 가정
또는 실제 실험으로 얻은 값을 넣고, 근거가 가정뿐이면 Decision Engine은 `EXPERIMENT`로 제한한다.

사용자가 비율을 모를 때 보수·기준·낙관 계획 시나리오가 필요하면 각 숫자는 `ENGINE` 또는 Tool
Contract의 `DEFAULT` 출처여야 한다. 현재 계약은 이 계획 숫자의 기본값을 제공하지 않으므로,
둘 중 어느 출처에도 값이 없으면 `MISSING_INPUT`으로 전환한다. LLM은 계획용 숫자를 생성하지 않는다.

### `compare_price_strategies_with_forecast`

Agent용 도구는 `location` 객체로 다음 세 위치 입력 형식 중 정확히 하나를 받는다.

- `{"address": "주소 검색 문자열"}`
- `{"latitude": 37.5665, "longitude": 126.978}`
- `{"kma_nx": 60, "kma_ny": 127}`

형식을 섞거나 일부 필드만 전달하면 `INVALID_LOCATION`이다. 주소 검색 문자열은 좌표와 격자를
확인한 뒤 폐기하며 도구 결과, Agent 세션, 감사 로그에 남기지 않는다. 도구 결과에는 위치 입력
종류와 기상청 격자만 포함하고 정확한 위도·경도도 반환하지 않는다.

## 도구 등록에 사용할 코드

- 함수 스키마: `src.agent_tool_contracts.TOOL_SCHEMAS`
- 호출 디스패처: `src.agent_tool_contracts.execute_tool`
- 계산 서비스: `src.decision_service.MarginCastDecisionService`

에이전트 런타임에서 함수 호출을 받으면 `execute_tool(tool_name, arguments)`에 전달한다. 현재
Mock Runtime은 성공 결과만 Provider의 후속 설명과 Response Policy에 전달한다. 오류 결과는 즉시
`AgentResultRouter`로 보내 부족 입력 또는 공통 Agent 오류로 변환한다.

E단계의 `OllamaProvider`도 같은 `LLMProvider.generate(messages, tools) -> LLMResponse` 계약을
사용한다. 공통 `TOOL_SCHEMAS`를 Ollama 함수 도구 형식으로 바꾸고, Ollama 응답만 공통 `ToolCall`과
`LLMResponse`로 변환한다. 계산 서비스와 `execute_tool`은 호출하지 않는다. Tool 실행 책임은 계속
`AgentRuntime`에 있다.

최종 Ollama 응답은 JSON Schema를 사용해 다음 세 필드로 제한한다.

- `decision_claim`: `RECOMMEND`, `EXPERIMENT`, `HOLD` 중 하나
- `explanation`: 숫자를 새로 만들지 않는 정성 설명
- `next_action`: 숫자를 새로 만들지 않는 정성적 다음 행동

사용자 확인값은 공통 `Message.context`의 Conversation State로 Provider에 전달한다. 모델이 필수
Tool 인자를 빠뜨렸을 때 해당 값이 State에 있으면 `PROVIDER_INVALID_TOOL_CALL`이고, State에도
없으면 사용자에게 묻는 `MissingInput`이다. 자동 재시도는 하지 않는다.

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
3. 기대값과 함께 범위·성공확률·하방 위험·근거 품질·실행 판단을 보여준다.
4. 실제 예보를 사용하지 않았다면 그 사실을 알린다.
5. 지원되지 않는 메뉴에는 가격탄력성을 추측하지 않는다.
6. 세트 전략의 신규 수요와 잠식은 사용자 가정임을 분명히 표시한다.
7. `data_provenance.warning`을 생략하지 않고 합성 데이터 기반 프로토타입임을 알린다.
8. `evidence_quality.validation.empirically_calibrated`가 `false`이면 미검증 휴리스틱이라고 설명한다.
9. 실제 예보를 사용했다면 적용 날짜와 날씨 문맥을 알린다.
10. `menu_specific_causal_effect_validated=false`이면 특정 날씨가 판매량 증가·감소를 일으킨다고 단정하지 않는다.
11. 미지원 메뉴에는 오류 세부정보의 `next_step`을 사용해 데이터 확보 경로를 안내한다.

현재 설명문 안의 아라비아 숫자 탐지는 C/D단계 Mock 방어선으로 유지한다. E단계의 핵심 경계는
ToolResult에서 고정한 facts, 구조화된 Ollama 최종 응답, Response Policy다. 한국어 숫자 파서는
추가하지 않는다. facts의 `source_ref`와 `fact_provenance.ref`는 현재 둘 다 유지하며 Response
Policy가 일치 여부를 검사한다.

실제 로컬 서버 테스트는 기본 회귀 테스트에서 제외한다. Ollama 서버와 Tool Calling 지원 모델을
준비한 뒤 다음처럼 명시적으로 실행한다.

```powershell
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:OLLAMA_MODEL="<tool-calling-model>"
$env:RUN_OLLAMA_INTEGRATION="1"
python -m unittest tests.test_ollama_integration
```

Decision Engine의 데이터 출처, 근거 품질 산식과 검증 계획은
[Decision Engine 데이터 출처와 판단 방법](decision-methodology.md)을 기준으로 한다.

## 사용자에게 남은 구현

1. 사용할 LLM과 에이전트 프레임워크 선택
2. 시스템 프롬프트와 질문 흐름 작성
3. `TOOL_SCHEMAS` 등록
4. 함수 호출을 `execute_tool`로 전달
5. 도구 결과를 사용자가 이해할 수 있는 경영 언어로 설명

A단계 Tool Contract, B단계 Mock 세로 흐름, C단계 가격 Response Policy, D단계 Missing Input과
도구 오류 분기, E단계 Ollama Provider까지 완료됐다. 복구·재시도 정책은 아직 연결하지 않는다.

Agent 본체의 필수 회귀 사례는 [MarginCast Agent MVP 평가 시나리오](agent-evaluation.md)에
정리했다.
