# Agent ToolResult fixtures

이 디렉터리의 결과 JSON은 직접 작성한 Mock이 아니다. 현재 체크아웃의
`src.agent_tool_contracts.execute_tool()`을 호출해 생성한다.

```powershell
.\.venv\Scripts\python.exe tests\agent\fixtures\generate_fixtures.py
```

정확한 `tool_name`과 `arguments`는 [fixture-inputs.json](fixture-inputs.json)에 기록한다.
가격 fixture는 `horizon_days`, `simulations`, `seed`를 일부러 생략해 엔진의
`execution_defaults`가 적용되는 경로를 사용한다.

## Fixture 목적

| 파일 | 실제 호출 결과 |
|---|---|
| `capabilities.json` | 지원 메뉴, 한도, 데이터 출처와 도구별 실행 기본값 |
| `price_recommend.json` | M01 가격 인상 후보의 최종 판단 `RECOMMEND` |
| `price_experiment.json` | M02 가격 인상 후보의 최종 판단 `EXPERIMENT` |
| `price_hold.json` | M01 가격 인하 후보의 최종 판단 `HOLD` |
| `unsupported_menu.json` | 가격 변화 근거가 부족한 M04의 `UNSUPPORTED_MENU` 오류 |
| `invalid_scenario.json` | 할인액이 정가와 같은 입력의 `INVALID_SCENARIO` 오류 |

생성기는 위 판단과 오류 코드, capabilities의 기본값을 검증한 후에만 파일을 쓴다. 엔진 정책이나
데이터가 바뀌어 결과 분류가 달라지면 조용히 fixture를 덮어쓰지 않고 실패한다.

## 가격 ToolResult 필드 위치

`strategies[]`에는 현재 가격 기준 행과 사용자가 입력한 후보 행이 함께 들어간다. 후보를 이름으로
찾은 뒤 아래 경로에서 수치를 읽는다.

| Agent에서 사용할 의미 | ToolResult 경로 |
|---|---|
| 예상 판매량 `expected_units` | `strategies[].units.mean` |
| 예상 기여이익 `expected_contribution_profit` | `strategies[].contribution_profit.mean` |
| 현재 가격 대비 이익 변화 `profit_delta` | `strategies[].profit_delta.mean` |
| 백분위·예상 구간 | `strategies[].units`, `contribution_profit`, `profit_delta`의 `p05`, `p10`, `p50`, `p90`, `p95`; 기본 80% 구간은 `p10`~`p90` |
| 개선확률 `success_probability` | `strategies[].success_probability` |
| 하방 위험 `downside_risk` | `strategies[].downside_risk` 및 `strategies[].decision.downside_risk` |
| 근거 품질 `evidence_quality` | `strategies[].evidence_quality` |
| 후보별 판단 `decision` | `strategies[].decision` |
| 데이터 출처 | 최상위 `data_provenance` |
| 최종 선택 시나리오 | `recommended_action.name`과 `recommended_action.action` |
| 위험 조정 순위 | `decision_ranking[]`; `rank == 1`이 최종 선택과 대응 |
| 기대이익만 본 1위 | `highest_expected_profit`; 최종 선택과 다를 수 있음 |

기준 행은 `strategies[].is_reference == true`이며 `success_probability`, `evidence_quality`,
`decision`이 `null`이므로 후보 판단에 사용하지 않는다.

## Capabilities 기본값

`capabilities.json`의 `execution_defaults`에는 다음 키가 있다.

- `compare_price_strategies`
- `compare_price_strategies_with_forecast`
- `simulate_bundle_strategy`

Agent와 시스템 프롬프트는 해당 값을 별도 상수로 복제하지 않고 이 응답을 사용한다.
