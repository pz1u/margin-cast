# MarginCast HTTP API

`src.http_api`는 MarginCast 계산 엔진, Agent 대화 경계와 정적 웹사이트를 Python 표준 라이브러리
HTTP 서버로 제공한다. Agent도 계산 구현을 직접 호출하지 않고 `src.agent_tool_contracts`의
검증된 Tool Registry와 `execute_tool()` 경계를 사용한다.

## 실행

```powershell
.\.venv\Scripts\python.exe -m src.http_api
```

기본 주소는 `http://127.0.0.1:8000`이다. 같은 서버에서 웹사이트와 `/api/*` JSON API를
제공하므로 별도 CORS 설정이 필요하지 않다.

전략 응답의 `data_provenance`는 현재 데이터가 `synthetic-pos-v2`이며 실제 매장 데이터가
아님을 명시한다. `evidence_quality`는 성공확률과 다른 `heuristic-v1` 근거 품질 점수이고,
`formula_fingerprint`로 산식 정책을 식별한다. 실제 매장 결과와의 관계는 아직 검증되지
않았다. 산식과 해석은
[Decision Engine 데이터 출처와 판단 방법](decision-methodology.md)을 참고한다.

## 경로

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/health` | 서버 버전, 실행 상태와 로딩된 근거 품질 버전·지문 |
| `POST` | `/api/agent/chat` | 가격 질문 한 건 또는 동일 세션의 부족 입력 후속 대화 |
| `GET` | `/api/capabilities` | 지원 메뉴, 데이터 범위, 입력 한도 |
| `POST` | `/api/strategies/price` | 가격·할인 전략 비교 |
| `POST` | `/api/strategies/price/forecast` | 매장 주소의 실제 단기예보를 반영한 가격·할인 비교 |
| `POST` | `/api/strategies/bundle` | 세트 전략 시뮬레이션 |
| `GET` | `/api/store/profile` | POS 연동 상태, 엔진 수수료(읽기 전용), 메뉴별 원가·원가율·분석 상태 |
| `POST` | `/api/store/menus` | 새 메뉴 추가(메뉴명·카테고리·판매가·식재료 원가·판매 채널) |
| `POST` | `/api/store/menus/cost` | 메뉴 식재료 원가 수정 또는 `reset`으로 POS 원가 복원 |
| `POST` | `/api/store/menus/delete` | 직접 추가한 메뉴 삭제 |
| `POST` | `/api/store/bundle/observation` | 선택한 주메뉴·구성 메뉴의 POS 관측값과 식별 불가 값 |
| `POST` | `/api/store/bundle/simulate` | 주메뉴·구성 메뉴를 선택한 세트 시뮬레이션 |
| `POST` | `/api/experiments/plans` | 실행 전 가격 실험의 예측 분포 저장 |
| `GET` | `/api/experiments/plans` | 실제 결과 입력을 기다리는 실험 계획 조회 |
| `POST` | `/api/experiments/feedback` | 저장한 계획에 실제 결과 연결 |
| `GET` | `/api/experiments/feedback/summary` | 누적 오차·80% 구간 포함률 조회 |

가격 비교 본문은 Agent 도구의 `compare_price_strategies`와 같다.

```json
{
  "menu_id": "M01",
  "scenarios": [
    {"name": "500원 인상", "list_price": 9500, "discount": 0}
  ],
  "horizon_days": 14,
  "simulations": 10000,
  "seed": 42
}
```

실제 예보 경로에는 같은 본문에 `address`를 추가한다. 이 경로는 주소를 카카오 로컬 API로
WGS84 좌표로 변환하고 기상청 단기예보를 조회한 뒤 계산 엔진에 전달한다. 단기예보가 실제로
제공하는 범위만 사용하도록 `horizon_days`는 1~5일로 제한한다. 현재 시각의 예보가 부족하면
`INSUFFICIENT_FORECAST` 오류를 반환한다. 응답의 `weather.applied_from`과 `applied_to`는 실제
계산에 사용한 날짜 범위이며, `available_from`과 `available_to`는 조회된 전체 범위다. Agent 함수
도구 계약에는 주소 필드를 추가하지 않았다.

## 매장 설정과 유효 원가

`/api/store/*`는 화면 전용 경로이며 Agent 도구 계약(`agent_tool_contracts`)은 바꾸지 않는다.
`/api/strategies/bundle`은 기존처럼 `scenario`, `horizon_days`, `simulations`, `seed`만 받고
치킨마요·콜라 세트로 고정된다. 메뉴를 고르는 세트 계산은 `/api/store/bundle/simulate`
(`main_menu_id`, `component_menu_ids`, `scenario`, `horizon_days`, `simulations`, `seed` 모두 필수)를 쓴다.

식재료 원가는 `StoreProfileService.effective_unit_cost(menu_id)`가 결정한다. 사용자 수정값이 있으면
`USER`, 없으면 `menu_cost_history`의 최신 값(`POS_HISTORY`)이다. 서버는 `USER` 원가를
`MarginCastDecisionService.set_cost_overrides_provider()`로 계산 엔진에 연결하며, 이 값은 가격·할인
시뮬레이션의 `unit_cost`와 세트 증거의 원가만 바꾼다. 수요 예측과 가격탄력성 추정은 원가를
사용하지 않으므로 변하지 않는다. 가격·세트 응답의 `cost_basis`는 사용한 원가와 출처를 담는다.
수수료율은 `generate_data`의 엔진 상수이며 API로 수정할 수 없다. 포장비는 엔진이 모델링하지 않는다.

신규 메뉴는 `analysis.status`가 `DATA_COLLECTING`이며 가격 비교와 세트 계산에서 거부된다.
POS 메뉴의 상태는 엔진 capabilities에서 가져와 `ANALYZABLE` 또는
`INSUFFICIENT_PRICE_VARIATION`으로 표시한다.

도메인 검증 실패도 JSON 오류 객체로 반환한다. 잘못된 입력은 `400`, 없는 경로는 `404`,
분석 패널이 준비되지 않은 경우는 `503`을 사용한다. 요청 본문은 64KiB로 제한한다.

## 배포 전 남은 항목

현재 서버 기본값은 로컬 접근만 허용한다. 공개 배포에서는 앞단 프록시의 TLS, 요청 제한,
접근 로그와 운영 오류 수집을 추가해야 한다. 브라우저에 제공하는 파일에는 기상청·카카오
서버 키를 포함하지 않는다.

실험 피드백은 반드시 계획을 먼저 저장한 뒤 해당 `feedback_id`에 실제 결과를 연결한다.
계획에는 당시 엔진·데이터·날씨·근거 품질 버전을 함께 저장한다.
요청 형식과 보정 지표는 [실험 피드백과 모델 보정 근거](experiment-feedback.md)에 정리했다.

## Agent 대화

요청은 현재 메시지와 선택적인 세션 ID만 받는다. `session_id`를 생략하면 서버가 생성해 응답한다.

```json
{
  "session_id": "demo-price-1",
  "message": "치킨마요를 9,500원으로 올리면 어때?"
}
```

Policy를 통과하면 `COMPLETED`이며, UI 핵심 수치는 LLM 문장이 아닌 `facts`에서 읽는다.
전체 ToolResult와 System Prompt는 반환하지 않는다.

ENGINE 결과가 정상이고 LLM presentation만 숫자 정책을 위반한 경우에는 Response Policy가
정적 설명으로 교체한 뒤 `COMPLETED`를 반환한다. 이때 `presentation.source`는
`POLICY_FALLBACK`이며 정상 LLM 설명은 `LLM`이다. UI는 이 값을 일반 사용자에게 표시하지 않고
개발 모드에서만 실행 추적에 사용한다. Decision, facts 또는 provenance 위반은 계속 `REJECTED`다.

```json
{
  "session_id": "demo-price-1",
  "status": "COMPLETED",
  "execution_id": "...",
  "recommendation_id": "...",
  "facts": {
    "expected_units": {"value": 604.8, "source": "ENGINE", "source_ref": "..."},
    "engine_decision": {"value": "EXPERIMENT", "source": "ENGINE", "source_ref": "..."}
  },
  "presentation": {
    "explanation": "정성적 설명",
    "next_action": "정성적 다음 행동"
  },
  "notices": ["합성 데이터 및 근거 품질 고지"]
}
```

가격이 없으면 HTTP 200과 `NEEDS_INPUT`을 반환한다.

```json
{
  "session_id": "demo-price-2",
  "message": "치킨마요 가격 올리면 어때?"
}
```

```json
{
  "session_id": "demo-price-2",
  "status": "NEEDS_INPUT",
  "execution_id": "...",
  "recommendation_id": null,
  "question": "변경할 가격은 얼마로 생각하고 계신가요?",
  "missing_input": {"fields": ["list_price"], "source_requirement": ["USER"]}
}
```

같은 `session_id`로 `{"message":"9500원"}`을 보내면 저장된 메뉴 문맥과 결합해 계산한다.
`REJECTED`는 Policy 위반 코드와 `execution_id`만 제공하며 LLM 설명을 노출하지 않는다.
예상하지 못한 `ERROR`는 일반 메시지만 반환하고 내부 예외나 stack trace를 숨긴다.

서버 메모리 세션에는 capabilities, 선택 메뉴, 작성 중인 가격 시나리오, pending question,
`recommendation_id`와 실험 연결용 추천 스냅샷만 저장한다. 전체 대화 원문은 저장하지 않는다.
서버 재시작 시 세션은 초기화된다. G1은 단일 프로세스·단일 worker 실행만 지원한다.

## Ollama 준비와 warm-up

코드는 모델명을 고정하지 않는다. 로컬 개발에서는 G.0에서 확인한 `qwen3:1.7b`를 사용할 수 있고,
`qwen3:4b`도 같은 Provider 계약으로 계속 지원한다.

```powershell
ollama serve
ollama pull qwen3:1.7b
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:OLLAMA_MODEL="qwen3:1.7b"
python -m src.generate_data
python -m src.prepare_analysis_data
python -m src.http_api
```

실제 데모 전에 위 Agent 요청을 한 번 보내 모델을 로드하고 `ollama ps`에서 warm 상태를 확인한다.
startup 과정은 모델 호출이나 사업 Tool 실행을 자동으로 수행하지 않는다.
