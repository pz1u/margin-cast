# MarginCast Agent 설계 계약

## 1. 범위와 경계

이 문서는 사용자와 합의한 Agent 설계다. Schema 클래스와 Provider Interface는
`src/agent_schemas.py`, `src/llm_provider.py`에 구현했다. 공급자 응답과 주입된 도구 실행기를
연결하는 최소 Loop는 `src/agent_runtime.py`에 구현했다. `src/agent_integration.py`가 기존
`TOOL_SCHEMAS`, `execute_tool()`과 `MarginCastDecisionService`를 Loop에 연결한다. 실제 계산
도구의 계약은 [agent-tool-contract.md](agent-tool-contract.md)를 따른다.
LLM에 등록하는 Tool Schema에서는 엔진 기본값이 있는 실행 설정을 선택 입력으로 노출하고,
Loop가 Dispatcher 호출 전에 해당 기본값을 명시적으로 채운다.

- Agent는 의도 파악, 필요한 입력 질문, 전략 후보 구성, 도구 호출, 결과 설명을 담당한다.
- 판매량·기여이익·탄력성·개선확률·예상 범위·신뢰도·위험도는 계산 엔진만 산출한다.
- `RECOMMEND`, `EXPERIMENT`, `HOLD`와 순위는 엔진 결과를 보존한다.
- UI의 핵심 값은 도구 결과에서 직접 가져온다. LLM 설명 문자열을 파싱하지 않는다.
- 설명은 짧고 친절한 한국어 존댓말로 작성한다. 성공확률은 현재 대비 기여이익이
  높을 확률이며, 근거 신뢰도는 별개인 근거 품질이다.
- 없는 수치·근거·실험 기간은 생성하지 않는다. 엔진에 없는 7일 실험 등을 권고하지 않는다.
- 사용자 주도로 작은 단위를 확인하며 진행한다. Agent Framework나 Factory 계층은 도입하지 않는다.

## 2. 값의 출처와 변경 권한

| 출처 | 의미 | 생성·변경 가능한 주체 |
| --- | --- | --- |
| `USER` | 사용자 입력·확인한 사업 조건과 가정 | 사용자. Agent는 뜻을 바꾸지 않는 추출만 수행 |
| `ENGINE` | 엔진이 산출한 수치·판단·관측 근거 | 계산 엔진. Agent와 UI는 참조·표시만 수행 |
| `DEFAULT` | 엔진이 정의한 기본 설정 | 엔진의 설정 정의. Agent는 명시적으로 적용 |
| `LLM` | 자연어 설명, 질문 표현, 미확정 전략 후보 | LLM. 실행 조건이나 엔진 사실로 승격 불가 |

중요한 값에는 `Provenance(source, ref)`를 연결하는 가벼운 필드별 매핑을 둔다.
`ref`는 사용자 메시지의 해당 입력, 도구 호출 ID와 결과 경로, 기본값 정의 위치 등을
가리킨다. 출처와 원본 위치를 한 객체로 묶어 둘 중 하나만 변경되는 상태를 막는다.

- 사용자 발언에서 LLM이 추출했더라도 원문으로 확인 가능한 9,500원은 `USER`다.
- LLM이 제안한 가격·전환율은 `LLM` 후보다. 사용자가 확인하기 전에는 필수 사업 입력으로
  사용할 수 없다. 확인 후에도 원래 후보와 사용자 확인 기록을 남긴다.
- 사용자의 전환율이 엔진의 `assumptions`로 되돌아와도 가정의 출처는 `USER`다.
  도구에서 반환됐다는 이유로 관측 근거로 바꾸지 않는다.
- `ENGINE`이어야 하는 값을 `LLM`이 생성할 수 없다. 출처 라벨을 붙이는 것만으로 검증된
  값이 되는 것도 아니다. 런타임은 실제 원본 경로와 값의 일치를 확인해야 한다.
- 호출 ID, 처리 상태, 캐시 시각, 오류 분류는 런타임이 만드는 제어 메타데이터다.
  네 출처는 사업 값과 설명의 출처이며, 제어 메타데이터를 LLM 사실로 취급하지 않는다.
- 금액 구분자·백분율 등 표시 형식은 코드가 처리하고 원본 정밀도는 보존한다.

## 3. 객체별 책임과 필드

아래는 논리 객체다. 각각을 독립 클래스로 구현할지는 Schema 단계에서 결정한다.

| 객체 | 주요 필드 | 책임·출처 | 생성·변경 주체 |
| --- | --- | --- | --- |
| `AgentInput` | `message_id`, `text`, `business_inputs`, `provenance` | 원문과 명시된 조건은 USER. 미확정 해석은 별도 보관 | 사용자 입력을 런타임이 기록 |
| `Strategy` | `name`, `operation`, `business_inputs`, `provenance`, `confirmed` | 대안의 조건. 설명·미확정 후보는 LLM, 실행 사업 값은 USER 또는 확인된 ENGINE 문맥 | LLM이 후보 제안, 사용자 확인, 런타임 검증 |
| `ToolCall` | `call_id`, `name`, `arguments`, `argument_provenance` | LLM의 도구 선택 요청. 사업 인자 출처와 DEFAULT 적용을 검증 | LLM 요청을 런타임이 검증·전달 |
| `ToolResult` | `call_id`, `tool_name`, `raw` | Dispatcher 성공·오류 원본을 가공 없이 보존 | Dispatcher 반환을 런타임이 기록 |
| `AgentError` | `code`, `message`, `origin`, `retryable` | 입력·Capability·Tool·Provider·Runtime 실패의 공통 사용자 상태 | 런타임이 원본 오류를 분류 |
| `MissingInput` | `fields`, `reason`, `question`, `strategy_ref` | 검증된 누락 필드와 필요한 이유. 질문 표현만 LLM 가능 | 런타임이 누락 확인, LLM 또는 고정 문구로 질문 |
| `Decision` | `action`, `reason`, `source_ref` | ENGINE 판단을 그대로 참조. 자체 재평가하지 않음 | 엔진 생성, 런타임 복사 |
| `Evidence` | `items`, `provenance`, `limitations` | 관측·모델 근거는 ENGINE. 가정은 원래 USER/DEFAULT 출처를 별도로 보존 | 엔진 응답에서 런타임이 선택·복사 |
| `AgentResponse` | `status`, `facts`, `decision`, `evidence`, `explanation`, `missing_input`, `error`, `tool_results` | 핵심 값은 ENGINE, 설명은 LLM, Tool 원본은 별도 보존 | 런타임이 원본과 설명을 조합 |

`StaticCapabilities`는 도구별 `tool_schemas`, `output_schemas`,
`required_user_inputs`, `execution_defaults`를 분리한다. `DynamicCapabilities`는 현재
`supported_menus`, `limits`, `data`, `limitations`와 향후 엔진이 제공할
`data_sufficiency`, `model_readiness`, `data_version`을 표현한다. 현재 제공되지 않는 동적
필드는 빈 객체나 `None`으로 남긴다.

`Evidence.items`의 각 항목은 이름·값·단위·대상 범위를 담고 같은 키의 `Provenance`가
출처와 원본 위치를 가리킨다.
엔진이 제공하지 않은 항목은 생략하거나 미제공으로 표시한다. 증거 없음은 0이 아니다.

`AgentResponse.status`는 `completed`, `needs_input`, `error`다. `HOLD`는 성공적으로
계산된 판단이며 오류가 아니다. 입력 부족·계산 실패 응답에는 해당 전략의 수치와 판단을
채우지 않는다. 이전 요청의 결과를 현재 결과로 재사용하지 않는다.

## 4. 필수 사업 입력과 기본 설정

도구별로 `required_user_inputs`와 `execution_defaults`를 분리한다.
기존 Dispatcher는 실행 설정도 필수로 받으므로 런타임이 기본값을 명시적으로 전달한다.

| 구분 | 현재 도구의 필드 | 규칙 |
| --- | --- | --- |
| 가격 대안의 사업 입력 | `menu_id`, `scenarios[].list_price`, 할인 대안의 `discount` | 메뉴명은 ENGINE 메뉴 정보에 매핑. 가격·할인액은 사용자 조건 확인 |
| 세트의 사업 입력 | `bundle_price`, `take_rate`, `copurchase_take_rate`, `incremental_demand_rate`, `cannibalization_rate` | 누락 시 질문. 근거 없는 비율을 추정하지 않음 |
| 대안 이름 | `name` | LLM이 설명용 이름 생성 가능. 계산 가정으로 사용하지 않음 |
| 실행 기본값 | `simulations=10000`, `seed=42` | 현재 `MarginCastDecisionService`의 기본값을 사용 |
| 비교 기간 | `horizon_days=14` | 엔진 기본값을 사용할 수 있으나 사업 해석에 영향을 주므로 사용자에게 표시. 명시된 기간 우선 |

단순 정가 인상 요청은 할인 없는 대안으로 정규화할 수 있다. 이때 `discount=0`은
일반적인 누락값 보충이 아니라 요청 의미에 따른 매핑으로 기록한다. 할인 여부가 모호하면
질문한다. 사용자 발언에 없는 사업 가정을 자동으로 채우는 일반 규칙은 두지 않는다.

현재 입력에는 `confidence_level`이 없다. 2,000회 반복이나 95% 설정을 엔진 기본값처럼
기록하지 않는다. 도구가 반환한 백분위를 표시하며 p10~p90은 시뮬레이션의 중앙 80% 범위다.
비교 기간은 권장 실험 기간이 아니다.

## 5. 정적 계약과 동적 Capabilities

| 종류 | 내용 | 획득·갱신 |
| --- | --- | --- |
| `StaticCapabilities` | 도구 이름, 입력 Schema, 출력 계약, 지원 기능 | 초기화 시 `TOOL_SCHEMAS`와 문서 계약으로 등록 |
| `DynamicCapabilities` | 현재 지원 메뉴, 기간 한도, 데이터 충분성, 모델 준비 여부 | 실제로 제공된 필드만 capability 응답에서 얻고 세션 캐시 |

현재 `TOOL_SCHEMAS`에는 입력 Schema만 있다. 기계 검증 가능한 출력 Schema는 아직 없다.
현재 capability 응답은 지원 메뉴·가격·기간 및 반복 한도·패널 범위를 제공한다.
데이터 충분성의 상세 상태, 모델 준비 여부, 데이터 버전은 명시적으로 제공하지 않는다.
지원 메뉴에 포함됐다는 사실을 모든 분석의 데이터가 충분하거나 모델 준비가 완료됐다는 뜻으로
확대하지 않는다.

- 매 요청마다 조회하지 않는다. 지원 여부나 메뉴 매핑이 필요하지만 캐시가 없으면 조회한다.
- 사용 가능한 캐시가 있으면 이를 이용한다. 전략 실행은 지원 정보를 확인한 뒤 진행한다.
- 데이터 버전 변경을 감지하면 캐시를 무효화한다. 단, 현재 `version`은 서비스 버전이며
  데이터 버전이 아니다. 실제 버전 신호·감지 시점은 후속 구현에서 정할 사항이다.
- 버전 신호가 없는 첫 버전은 데이터 상태를 세션 동안 고정해 사용하고, 데이터 교체 시
  세션을 새로 시작한다. 자동 변경 감지가 구현됐다고 표현하지 않는다.
- `UNSUPPORTED`나 `INSUFFICIENT_DATA`이면 캐시를 무효화하고 필요할 때 한 번 재확인한다.
- 일반 입력 오류에서는 캐시를 유지한다. 데이터/모델 상태와 관련된 `ENGINE_ERROR`에서는
  무효화하되 무조건 반복 조회하지 않는다.
- capability 실패 시 지원 상태를 추측하지 않는다. 근거가 필요한 계산을 중단하고 오류를 알린다.

## 6. 오류 분류와 복구

다음 다섯 종류는 `AgentError.code`의 공통 오류 분류다. 엔진의 원래 `code`, `message`,
`details`, `retryable`은 `ToolResult.raw`에 보존하며 기존 엔진 코드를 바꾸지 않는다.

| 분류 | 사용자 안내·동작 | 캐시 |
| --- | --- | --- |
| `MISSING_INPUT` | 필요한 사업 값만 질문. 모른다면 실험·자료 확보 필요 안내 | 유지 |
| `INVALID_INPUT` | 잘못된 값과 수정 조건 안내 | 유지 |
| `UNSUPPORTED` | 지원 범위 설명, 필요 시 재확인 | 무효화 |
| `INSUFFICIENT_DATA` | 데이터 부족 설명, 수치 생성 금지 | 무효화 |
| `ENGINE_ERROR` | 계산 실패 설명, 수치 생성 금지 | 데이터/모델 상태 관련일 때 무효화 |

현재 오류와의 매핑은 다음 기준으로 구현한다.

- 사전 Schema 검사에서 사업 필드 누락 → `MISSING_INPUT`.
- `INVALID_ARGUMENTS` 또는 `INVALID_SCENARIO`에 구조화된 `missing_fields`가 있으면
  해당 필드가 사업 입력인지 확인한다. 사업 입력 누락이면 `MISSING_INPUT`이다.
  실행 기본값 누락은 사용자 질문이 아닌 런타임 조립 문제로 처리한다.
- 값·범위 문제의 `INVALID_HORIZON`, `INVALID_SIMULATIONS`, `INVALID_SEED`,
  `INVALID_SCENARIO`, `INVALID_SCENARIOS` → `INVALID_INPUT`.
- `UNSUPPORTED_MENU` → 기본적으로 `UNSUPPORTED`. 현재 코드에서 가격 이력 부족이
  이유임을 설명할 수 있지만 별도의 데이터 부족 코드가 존재한다고 가정하지 않는다.
- `UNKNOWN_TOOL` → `UNSUPPORTED`. 이 경우 정적 계약을 확인하며 동적 메뉴 캐시는 유지한다.
- `PANEL_NOT_FOUND`, `INVALID_PANEL`, 예상하지 못한 엔진 예외 → `ENGINE_ERROR`.
  파일 부재·구조 오류를 통계적 표본 부족이라고 바꾸지 않는다.
- `INSUFFICIENT_DATA`는 명시적인 데이터 부족 상태를 받을 때 사용한다. 현재 Dispatcher에
  독립된 해당 코드가 없으므로 일반 `ValueError`나 문자열 추측만으로 매핑하지 않는다.

현재 세트의 중첩 필드 누락은 문자열 오류로 반환될 수 있으므로 사전 Schema 검사가 필요하다.
LLM이 잘못 조립한 JSON·도구 인자는 사용자 잘못으로 돌리지 않는다. 런타임은 제한된 횟수로
수정 기회를 주되 같은 오류를 반복하지 않는다. `retryable=true`도 무한 재호출 허용이 아니다.
Provider 연결 실패는 엔진 오류로 위장하지 않고 `AgentResponse.error`의 발생 위치를
`provider`로 기록한다. Provider 전용 오류 세분화는 연결 단계에서 정한다.

## 7. 계산 결과와 근거의 실제 매핑

가격 비교에서 선택된 비기준 전략을 `s`라고 할 때:

| UI/설명용 값 | 도구 원본 경로 | 출처 |
| --- | --- | --- |
| 예상 판매량·분포 | `s.units` | ENGINE |
| 기대 기여이익·분포 | `s.contribution_profit` | ENGINE |
| 현재 대비 기여이익 변화·범위 | `s.profit_delta` | ENGINE |
| 이익 개선 확률 | `s.success_probability` | ENGINE |
| 근거 신뢰도 | `s.confidence` | ENGINE |
| 위험·판단·사유 | `s.decision`, `s.downside_risk` | ENGINE |
| 신뢰도 구성 점수·관측 근거 | `s.confidence.components`, `s.confidence.evidence` | ENGINE |
| 추정 탄력성·표준오차 | `model.elasticity`, `model.elasticity_standard_error` | ENGINE |
| 실제 실행 설정 | `request.simulations`, `request.seed`, `request.horizon_days` | 실행 기록은 ENGINE, 입력 출처 USER/DEFAULT도 유지 |

최종 선택·순서는 `decision_ranking`과 `recommended_action`을 따른다.
`highest_expected_profit`만으로 실행 판단을 정하지 않는다.

세트 결과는 `strategy.profit_delta`, `strategy.success_probability`, `strategy.confidence`,
최상위 `decision`을 참조한다. 전체 기여이익이 제공되면 `strategy.scenario_contribution_profit`을
사용한다. `strategy.assumptions`의 비율은 사용자 가정임을 표시한다.

현재 가격 근거의 `price_events`는 학습 일별 데이터에서 기준 정가와 다른 구간이 시작한
횟수다. 독립적인 무작위 실험 횟수나 모든 가격 변경 횟수와 동일하지 않다.
`get_capabilities().data.rows`는 분석 패널 전체 행 수이며 주문 건수나 특정 메뉴의
모델 학습 표본 수가 아니다. 이 값들을 의미가 다른 `data_points`로 바꾸지 않는다.

공통 `data_version`, `model_version`, `price_change_experiments`, 주문 표본 수,
`recommended_experiment_days`는 현재 응답에 없다. Evidence에 임의로 추가하지 않는다.
서비스 내부에서 계산한 근거도 도구 응답에 없으면 Agent가 받은 근거라고 설명하지 않는다.

LLM 설명은 원본 근거와 한계만 요약한다. UI 값을 원본으로 분리해도 설명의 환각까지
자동으로 방지되지는 않는다. Mock 테스트에서 판단 변경·가공된 근거·없는 기간을 검증하고,
실제 LLM 연결 시 원본과 설명의 일치 검증 및 불일치 시 고정 안내로 대체하는 처리가 필요하다.
이는 후속 구현 요구사항이며 아직 구현된 보장이 아니다.

## 8. 흐름 예시 세 가지

아래는 설계 예시다. 계산을 실행한 결과가 아니며 임의의 예측 숫자를 넣지 않는다.

### 정상: 치킨마요 가격 인상

사용자: “치킨마요를 9,500원으로 올리면 어때?”

1. `AgentInput`: 원문과 `list_price=9500`을 USER로 기록한다.
2. `Strategy`: 동적 정보에서 치킨마요를 M01에 매핑한다. 정보가 없으면 capability를
   먼저 조회한다. 할인 없는 정가 인상이라는 요청 의미와 매핑 근거를 기록한다.
3. `ToolCall`: `compare_price_strategies`를 다음 인자로 실행한다.

```json
{
  "menu_id": "M01",
  "scenarios": [{"name": "9500원 정가", "list_price": 9500, "discount": 0}],
  "horizon_days": 14,
  "simulations": 10000,
  "seed": 42
}
```

4. `ToolResult`: `execute_tool()` 반환을 원본으로 보존한다. `status=ok`와 요구되는
   결과 필드, `model.ground_truth_used=false`를 확인한다.
5. `Decision + Evidence`: 해당 전략의 판단·사유와 `confidence` 근거를 원본에서 가져온다.
6. `AgentResponse`: `completed`. UI에는 실제 반환된 판매량·이익·범위·개선확률·신뢰도·
   판단을 표시한다. 비교 기간 14일과 최근 관측 문맥 재사용 가정을 함께 알린다.

ENGINE 판단이 `EXPERIMENT`일 때만 “계산 결과는 소규모 실험 단계입니다”라고 설명한다.
`RECOMMEND`는 실행 권고, `HOLD`는 보류로 설명하며 세 경우 모두 엔진 사유를 따른다.
어느 판단이 반환될지는 이 예시에서 미리 결정하지 않는다.

### 입력 부족: 세트 제안

사용자: “치킨마요 콜라 세트 만들면 어때?”

1. `Strategy`: 세트 후보만 구성한다. 가격·네 비율은 미확정으로 남긴다.
2. `MissingInput`: 현재 필요한 첫 질문을 `bundle_price`로 정한다.
3. `AgentResponse`: `needs_input`, 질문은 “생각하신 세트 판매가는 얼마인가요?”다.
   계산 도구는 실행하지 않으며 수치·Decision은 없다.
4. 사용자가 가격을 알려주면 같은 대화에서 보존하고, 남은 전환·신규 수요·잠식 가정을
   확인한다. 사용자에게 반복 횟수나 seed를 묻지 않는다.
5. 비율을 모른다면 “전환율을 정할 근거가 없어 아직 계산하기 어렵습니다. 작은 실험으로
   확인할 필요가 있습니다”라고 안내한다. 이 안내를 엔진의 `EXPERIMENT` 결과로 표시하지 않는다.

### 엔진 오류: 데이터 부족

이 예시는 Mock 엔진이 `INSUFFICIENT_DATA`를 명시적으로 반환하는 **가상 데이터 상태**다.
현재 저장소의 v2 데이터는 M02를 지원하며 현재 Dispatcher에는 이 독립 오류 코드가 없다.

1. 사용자 요청에 필요한 M02 가격 입력이 모두 있어 도구를 호출한다.
2. Mock `ToolResult`:

```json
{
  "status": "error",
  "error": {
    "code": "INSUFFICIENT_DATA",
    "message": "가격 변화 관측이 부족합니다.",
    "retryable": false,
    "details": {"menu_id": "M02"}
  }
}
```

3. Agent는 원본을 보존하고 공통 분류를 `INSUFFICIENT_DATA`로 기록한다.
   동적 캐시를 무효화하며 필요할 때 한 번 재확인한다. 같은 계산을 자동 반복하지 않는다.
4. `AgentResponse`: `error`, 해당 요청의 수치·Decision은 없다.
5. 안내: “현재 이 메뉴는 가격 변화 데이터가 부족해 신뢰할 수 있는 가격 시뮬레이션을
   제공하기 어렵습니다.”

실제 `PANEL_NOT_FOUND`라면 같은 오류 경로를 사용하되 분류는 `ENGINE_ERROR`, 안내는
“분석 데이터 파일을 찾지 못해 계산하지 못했습니다”가 된다. 두 원인을 혼동하지 않는다.

## 9. Provider와 Agent Loop 경계

```python
class LLMProvider:
    def generate(self, messages, tools) -> LLMResponse:
        ...
```

`src/llm_provider.py`의 실제 인터페이스다. 공통 `Message`는 역할·내용·도구 호출 ID와
도구 결과를 표현한다. `LLMResponse`는 `text`, `tool_calls`, `missing_input`을 갖는다.
Provider는 공급자별 메시지·도구 형식과 응답을 공통 형식으로 변환한다.
Provider는 도구를 실행하거나 Decision을 만들지 않는다.

```text
사용자 입력 → Agent Loop → LLMProvider.generate(messages, tools)
                          ← 공통 LLMResponse
             ↓ 입력 확인·출처 검증·지원 정보 확인
             ↓ execute_tool(name, arguments, service)
             ← ToolResult
             ├─ 원본 수치·Decision·Evidence → UI 데이터
             └─ 확인된 결과 문맥 → Provider → 설명
```

Agent Loop가 대화 기록·질문 후 재개·도구 실행·오류 처리·호출 상한을 관리한다.
한 턴의 여러 호출도 첫 버전은 순서대로 처리하고, capability 확인 전에 의존하는 계산을
실행하지 않는다. Loop는 Ollama 응답 JSON을 알 필요가 없다.

환경 설정은 `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=<사용자 선택 모델>`이다.
모델은 하드코딩하지 않는다. 처음에는 Mock Provider로 1~7단계를 검증하고,
Ollama Provider와 실제 로컬 모델 검증은 8~9단계에서 진행한다.
향후 공급자를 바꿀 때 해당 Provider와 설정 연결만 추가하며 Agent 핵심 흐름은 유지한다.

## 10. 다음 구현 단위와 확인 기준

1. Schema + Provider Interface: 필드·출처·누락 상태를 표현하는 최소 구조 구현 완료.
2. Mock LLM Loop: 정상·입력 부족·데이터 부족·도구 오류 흐름 구현 완료.
3. 기존 Dispatcher 연결: ENGINE 결과 보존과 capability 세션 캐시 구현 완료.
4. 정상 호출: 실제 계산 결과와 UI facts의 일치 확인.
5. Missing Input: 미확정 사업 입력의 실행 방지, 질문 후 재개 확인.
6. Tool Error: 오류 분류·캐시 처리·결과 미생성 확인.
7. 판단 설명: 세 Decision 보존, 확률/신뢰도 구분, 출처 없는 근거·기간 차단 확인.
8. Ollama Provider: 환경 설정과 공통 응답 변환 확인.
9. 로컬 end-to-end: 사용자 선택 모델로 실제 대화·도구 호출·설명 검증.

각 논리 단위를 검증 후 한국어 Conventional Commit으로 남긴다.
현재 구현은 실제 Dispatcher 연결까지 포함한다. UI facts 투영과 설명 일치 검증은 다음
논리 단위에서 진행한다.
