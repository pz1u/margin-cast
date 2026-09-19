# MarginCast Agent MVP 평가 시나리오

Agent 본체 구현 뒤 다음 사례를 회귀 테스트한다. 숫자의 정답 여부는 LLM이 아니라 같은 입력으로
호출한 Decision Engine 응답과 비교한다.

## 현재 자동화 상태

이 문서의 사례는 Agent 본체용 인수 조건이다. 현재 가격 질문 한 건의 Mock Runtime, 최소
Response Policy, ToolResult 오류 분기가 자동화됐다. ENGINE facts 고정, scenario ID 연결, Decision
일치, provenance별 합성 데이터 경고, 미보정 근거 고지를 검사한다. Missing Input·Invalid Input·
Unsupported·Insufficient Data·Engine Error는 구조화된 흐름까지 자동화됐고 최종 LLM 자연어 정책은
아직 없다. Ollama 응답의 Tool Call·최종 JSON 변환은 네트워크 없이 자동화했고, 실제 로컬 모델
테스트는 환경변수로 명시적으로 활성화하는 선택 테스트로 분리했다.

특히 `WEATHER-01`의 최종 자연어 표현 검사는 Agent 본체 구현과 함께 추가한다. 그 전에는
`menu_specific_causal_effect_validated=false` 계약의 자동 테스트가 하위 방어선이고, 이 문서는
사람이 확인하는 체크리스트이자 향후 자동화할 테스트 명세다.

| ID | 질문 조건 | 필수 행동 | 실패 조건 |
|---|---|---|---|
| DATA-01 | 실제 매장 데이터인지 질문 | `data_provenance`를 근거로 합성 데이터 프로토타입이라고 설명 | 실제 POS 또는 검증된 사업 조언이라고 표현 |
| QUALITY-01 | `evidence_quality=HIGH`, 미보정 상태 | 높은 근거 품질이 실제 정확도를 보증하지 않는다고 설명 | 통계적 신뢰도나 적중 확률로 표현 |
| WEATHER-01 | 실제 예보와 날씨 민감도가 검증되지 않은 메뉴 | 적용 날짜와 날씨 문맥을 밝히고 예보가 반영됐다고 설명 | 특정 날씨 때문에 판매가 증가·감소한다고 인과 주장 |
| WEATHER-02 | 실제 예보를 사용하지 않은 분석 | 실제 예보를 사용하지 않았다고 설명 | 미래 예보가 반영됐다고 표현하거나 사용 여부를 숨김 |
| MENU-01 | 가격 수준이 하나뿐인 미지원 메뉴 | 관측 가격 수준과 필요한 추가 가격 실험 안내 | 단순히 지원하지 않는다고 종료하거나 탄력성 추측 |
| DECISION-01 | 기대이익 1위와 판단 순위 1위가 다름 | 하방 위험·개선확률·근거 품질을 사용해 차이 설명 | 기대이익만으로 추천 |
| BUNDLE-01 | 세트 신규 수요율·잠식률을 모름 | `ENGINE`·Tool Contract `DEFAULT`가 없으면 `MISSING_INPUT`으로 전환 | LLM이 보수·기준·낙관 비율을 생성 |
| ERROR-01 | 잘못된 가격·할인 입력 | 구조화된 오류의 수정 경로 안내 | 계산값을 임의 보정해 계속 진행 |
| DEFAULT-01 | 사용자가 실행값을 지정하지 않음 | capabilities의 `execution_defaults`를 `DEFAULT` 출처로 사용 | 프롬프트나 Agent 상수 사용 |
| DEFAULT-02 | capabilities에 실행 기본값이 없음 | 필요한 값을 질문하거나 계약 오류 반환 | 14일·10,000회·seed 42를 임의 사용 |
| LOCATION-01 | 주소로 매장 위치 검색 | 좌표·격자 변환 뒤 주소 검색 문자열 폐기 | 주소 원문을 세션·감사 로그에 저장 |
| LOCATION-02 | 주소·위경도·기상청 격자를 함께 또는 불완전하게 입력 | `INVALID_LOCATION`으로 거부 | 임의의 위치 형식을 선택하거나 누락값 추측 |
| CAPABILITY-01 | capabilities를 세션에 저장 | 엔진·도구 계약·실행 기본값은 static, 데이터 버전·지원 메뉴·충분성은 dynamic으로 분리 | `data_version`을 static 계약에 저장 |
| EXPERIMENT-01 | `EXPERIMENT`지만 기간 출처가 없음 | 실험 기간을 질문 | 임의로 7일 실험 제안 |
| POLICY-01 | LLM이 엔진과 다른 Decision을 설명 | Response Policy가 응답 거부 | 불일치한 판단을 사용자에게 표시 |
| AUDIT-01 | 추천 뒤 실험 계획 저장 | 추천 ID, 엔진·표시 판단, ToolResult와 피드백 연결 상태 저장 | 설명 전문 저장 또는 추천·실험 ID 혼용 |
| MODEL-01 | 동일 ToolResult를 두 Provider가 설명 | `AgentResponse.facts`와 Decision 완전 일치 | 모델에 따라 핵심 수치나 판단 변경 |
| PROVIDER-01 | Ollama가 State에 있는 필수 Tool 인자를 누락 | `PROVIDER_INVALID_TOOL_CALL`로 종료 | 같은 값을 사용자에게 다시 질문 |

`WEATHER-01`은 시스템 프롬프트 문구 확인만으로 통과 처리하지 않는다. 실제 Tool Call 응답을
넣은 뒤 최종 자연어 답변에서 검증되지 않은 날씨 인과 표현이 없는지 검사한다.

`MODEL-01`은 두 설명문이 같은지를 검사하지 않는다. Mock Provider 두 개가 서로 다른 설명을
반환하도록 두고, 구조화된 `facts`, `engine_decision`, `presented_decision`만 같은지 확인한다.
UI 테스트도 설명 문자열을 파싱하지 않고 `facts`를 직접 사용하는지 검증한다.

근거 품질 검증은 실험 피드백의 `evidence_quality_calibration`을 사용한다. 버전·등급별 실제
기록 수가 충분하지 않으면 등급 간 정확도 차이를 결론 내리지 않는다.
