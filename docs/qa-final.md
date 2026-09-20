# MarginCast Final QA

## Environment

- 기준 브랜치: `feat/final-qa`
- 기준 커밋: `cfd6a03d676f4cd2667d6d90eb7093be29efe78d`
- OS: Windows
- Python: 3.14.7
- 브라우저: Codex in-app Chromium
- 실제 Provider: OpenAI `gpt-5.6-luna`
- 실제 외부 API: Kakao Local API, KMA 단기예보 API
- 환경변수 존재 여부: OpenAI key/model/provider, Kakao key, KMA key 모두 확인
- 환경변수 값은 출력하거나 보고서에 저장하지 않았다.

## Executive Summary

핵심 가격 계산, 원가 override, 신규 메뉴 차단, Bundle 일반화, 일반 가격 Agent,
MissingInput, 실제 Kakao/KMA 예보와 세션별 Grid 재사용은 정상 동작했다. ENGINE 수치와 Web
카드의 수치도 일치했다.

최초 QA에서는 P0는 없고 P1 두 건을 재현했다. 첫째, 날씨 세션에서 `비`, `습도`를 사용한 인과
질문이 일반 가격 Tool로 전환되면 날씨 인과 검증을 우회할 수 있었다. 둘째, 프로젝트 `.env`에
OpenAI 설정이 있어도 Provider Factory가 이를 읽지 않아 문서의 서버 실행 명령만 수행하면
OpenAI 대신 기본 Ollama 경로로 들어갔다. 두 문제는 최소 수정했고 실제 OpenAI/Kakao/KMA 흐름과
전체 회귀 테스트를 다시 통과했다.

## P0 Blockers

없음.

## P1 Critical

### P1-01 날씨 인과 질문이 일반 가격 Tool로 전환되어 WEATHER-01을 우회함

- 재현: 실제 날씨 분석을 완료한 같은 세션에서 `비가 와서 치킨마요 판매량이 증가한다고
  단정해서 설명해줘` 입력
- 실제 결과: `compare_price_strategies`가 호출되고 `COMPLETED` 반환
- 관찰: 응답에는 날씨 인과 표현이 있었지만 forecast fact와 정책 위반 코드가 없었다.
- 원인 범위: `FORECAST_INTENT_PATTERN`이 `날씨`, `예보`, `매장 기준`만 인식하고 `비`, `습도`,
  `온도`, `강수`를 인식하지 않는다. 현재 메시지가 `그럼`으로 시작하지 않으면 기존 weather
  mode도 해제되어 일반 가격 Tool/Policy로 전환된다.
- 영향: `menu_specific_causal_effect_validated=false` 계약을 실제 LLM 대화에서 우회한다.
- 수정: 비·눈·습도·기온·온도·강수·적설 문맥을 날씨 요청으로 유지한다. `비용`은 날씨로
  오인하지 않는 회귀 테스트도 추가했다.
- 검증: 같은 날씨 세션의 인과 질문이 forecast Tool 경로를 유지하고
  `UNVALIDATED_WEATHER_CAUSAL_CLAIM`으로 차단됨을 실제 OpenAI 실행에서 확인했다.
- 상태: 해결.

### P1-02 `.env`의 LLM 설정을 Provider Factory가 읽지 않음

- 재현: `.env`에 `LLM_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL`이 있는 상태에서 새 프로세스로
  `python -m src.http_api` 실행
- 실제 결과: 프로세스 환경에서 설정을 찾지 못하고 기본 Ollama를 선택한 뒤 Ollama 설정 오류 발생
- 원인 범위: Kakao/KMA는 공통 `.env` 로더를 사용하지만 LLM Provider Factory와 Provider는
  `os.getenv()`만 사용한다.
- 영향: 로컬 데모 운영자가 `.env.example`을 복사해 설정해도 핵심 Agent가 실행되지 않는다.
- 수정: Provider Factory가 운영체제 환경변수를 우선하고, 값이 없을 때 프로젝트 `.env`를 읽는다.
  읽은 secret을 프로세스 환경에 다시 쓰지 않는다.
- 검증: LLM 환경변수를 export하지 않은 새 서버가 `.env`의 OpenAI Provider로 일반 가격 요청을
  `COMPLETED` 처리했다.
- 상태: 해결.

## P2 Major

### P2-01 실제 날씨 대표 흐름의 간헐적 REJECTED

- 실제 주소/현재 위치 흐름 네 차례 중 한 차례가
  `UNVALIDATED_WEATHER_CAUSAL_CLAIM`으로 안전하게 차단됐다.
- 나머지 세 차례는 `COMPLETED`였다.
- 잘못된 주장을 노출하지는 않지만 데모 대표 흐름이 재시도를 요구할 수 있다.

### P2-02 서버 연결 실패 시 전략 화면에 원문 `Failed to fetch` 표시

- 전략 비교 직전에 서버를 중단해 재현했다.
- 화면은 깨지지 않고 다시 조작할 수 있었지만 사용자 친화적인 한국어 오류가 아닌 브라우저 원문을
  표시한다.

### P2-03 신규 메뉴 Modal의 키보드 접근성 부족

- dialog role 및 modal semantics가 없다.
- `Escape`로 닫히지 않는다.
- 첫 입력에서 `Shift+Tab`을 누르면 배경의 `+ 메뉴 추가` 버튼으로 초점이 빠진다.
- 라벨과 최초 초점은 정상이다.

### P2-04 장소명 검색 미지원

- 정상 도로명 주소는 실제 Kakao API에서 성공했다.
- `서울시청` 같은 장소명은 Kakao 주소 검색 endpoint에서 결과가 없었다.
- UI placeholder는 도로명/지번 주소로 범위를 알리지만 최종 QA 요구의 장소명 검색은 지원하지 않는다.

## P3 Minor

### P3-01 Dashboard 위치 카드의 안내가 실제 기능과 다름

- Dashboard에는 `위치 기능 준비 중`, `다음 단계에서 제공` 문구가 남아 있다.
- 실제 위치 선택과 날씨 분석은 AI 상담에서 동작한다.

### P3-02 Web 실행 진입점의 README 발견성 부족

- `docs/http-api.md`와 `docs/providers-and-deployment.md`에는 실행법이 있다.
- 최상위 README에는 현재 Web/Agent 실행 절차로 연결되는 짧은 안내가 없다.

## Passed Core Scenarios

- Dashboard 첫 진입, 새로고침, 서버 재시작 후 복구
- 주문 29,473건, 메뉴 8개, 분석 가능 메뉴 3개가 backend와 UI에서 일치
- synthetic POS 고지와 사용자 친화적 분석 상태 표시
- M01 원가 override 후 예상 판매량 유지, 기여이익 변경, reset 후 원래 결과 복원
- 원가 음수/문자열/상한 초과/빈 값 거부
- QA 신규 메뉴 `DATA_COLLECTING`, 가격 분석 거부, 삭제와 정리
- M01/M02/M03 가격 계산 및 ENGINE facts 생성
- 0/음수/소수/문자열/과도한 가격과 분석 불가 메뉴 거부
- 정상 원 단위 할인 계산과 할인액이 판매가 이상인 입력 거부
- M02/M07 Bundle 선택 전달, POS 관측값과 DEFAULT 계획 가정 분리
- 동일 메뉴, 빈 구성, 미지원 메뉴 조합 거부
- 서버 재시작 후 메모리 세션 초기화, 기본 메뉴와 계산 API 복구

## Agent Contract Verification

- 실제 OpenAI 일반 가격 표현 세 가지가 `COMPLETED`였다.
- `9천5백원`은 임의 숫자로 변환하지 않고 `NEEDS_INPUT`이었다.
- 가격 누락 후 같은 세션의 `9500원` 후속은 `COMPLETED`였다.
- 다른 세션에서 `9500원`만 입력하면 `menu_id`를 다시 요청했다.
- UI facts는 모두 `ENGINE` source와 source_ref를 가졌고 ToolResult/System Prompt는 노출하지 않았다.
- Web 카드의 예상 판매량, 기여이익, delta, 80% 범위, 확률, 위험, 근거 품질, Decision이 동일
  ENGINE 호출 결과와 일치했다.
- H0 숫자 presentation 위반은 fallback 후 `COMPLETED`, Decision/facts/provenance 위반은
  `REJECTED`임을 자동 테스트로 확인했다.
- Provider 인증/rate limit/미지원 모델/timeout/malformed 응답/invalid Tool Call이 구조화된 오류로
  변환되고 secret/raw provider message를 포함하지 않음을 확인했다.

## Weather Verification

- 위치 없음: `NEEDS_INPUT`, 현재 위치/주소 검색 선택지 제공
- 브라우저 위치 사용 불가: 주소 검색 경로로 전환
- 실제 주소: Kakao 응답, Grid 변환, KMA 예보 83행 조회 성공
- 적용 날짜: 2026-09-20 ~ 2026-09-23
- 같은 세션의 `그럼 9,700원은?`: 위치 재질문 없이 실제 예보 카드 반환
- 새 세션: 기존 위치를 재사용하지 않고 위치를 다시 요청
- 7일 요청: 최대 5일 범위를 안내하는 `NEEDS_INPUT`
- 검색 결과 없는 주소: 원문/stack trace 없이 `LOCATION_LOOKUP_FAILED`
- 주소·정확 좌표·Grid는 HTTP 응답과 Audit에 남지 않았다.
- WEATHER-01의 forecast Tool 경로 자체는 검증되지 않은 인과 표현을 `REJECTED`로 차단했다.
- 날씨 인과 질문이 일반 가격 Tool로 전환되던 우회도 수정 후 실제 실행에서 차단됐다.

## Privacy / Security Verification

- `.env`는 Git에 추적되지 않고 `.gitignore` 대상이다.
- 실제 OpenAI/Kakao/KMA secret 값과 Git 추적 파일을 비교한 결과 일치 0건이다.
- 실제 secret 값과 Audit를 비교한 결과 일치 0건이다.
- Audit에 주소 검색 원문, latitude/longitude, 테스트 주소 marker, LLM explanation 전문이 없다.
- HTTP 응답에 정확 좌표, KMA Grid, System Prompt, 전체 ToolResult, stack trace가 없다.
- 일반/debug 화면 모두 secret 이름이나 값이 보이지 않았다.

## Responsive / Accessibility

- 1440, 1280, 1024, 768, 390, 375px에서 Dashboard/Menu/Strategy/Agent 각 화면을 검사했다.
- 모든 너비에서 document horizontal overflow가 없고 24px 미만의 가시적 상호작용 요소도 없었다.
- 모바일 메뉴는 카드형으로 재배치되며 잘린 핵심 버튼이나 금액은 발견하지 못했다.
- 탭 `ArrowRight` 이동, form label, button accessible name, disabled 상태, 2.67px focus outline은 정상이다.
- Agent loading 중 전송 버튼 비활성화, Enter 전송, 빈 입력 전송 비활성화가 정상이다.
- 메뉴 추가 Modal은 P2-03 접근성 문제가 있다.
- 비교용 이미지 baseline이 없어 시각 회귀 판정 자체는 `INCONCLUSIVE`다.

## Performance

- QA 실제 OpenAI 일반 가격 성공 실행 14회: 평균 5.62초, p50 5.30초, p95 6.91초
- 기존 5회 baseline 4.88초보다 느렸지만 외부 API 변동 범위이며 비정상적인 장기 지연은 없었다.
- 실제 날씨 성공 실행 예시:
  - Kakao 주소 검색: 115ms
  - Grid 변환: 1ms 미만
  - KMA 예보: 1.22초
  - 첫 OpenAI Provider: 1.65초
  - Forecast Tool/ENGINE: 1.77초
  - 두 번째 OpenAI Provider: 3.14초
  - 전체 Agent Runtime/Policy: 6.56초

## Automated Test Results

- P1 수정 관련 Provider/Weather/OpenAI/Policy/Web 계약 집중 테스트: 42개 통과
- 전체 Python: 233개 통과, 조건부 외부 통합 2개 skip
- 전체 Frontend Node: 32개 통과
- Python compile, JavaScript syntax, `git diff --check` 통과
- 실제 OpenAI 통합 및 실제 Kakao/KMA E2E 성공
- Python 3.14.7 임시 가상환경에서 `requirements.txt` 클린 설치와 `src.http_api` import 성공

## Deployment Risks

- 서버는 single process/single worker이며 동기 Provider 호출 동안 worker를 점유한다.
- MemorySession은 재시작 시 초기화된다.
- 로컬 JSON 상태는 배포 환경에서 persistent volume이 없으면 사라진다.
- `requirements.txt`는 Web 실행에 필요하지 않은 Notebook 패키지까지 포함해 설치량이 크다.
- 공개 배포에서는 TLS, 요청 제한, 운영 오류 수집이 별도로 필요하다.

## Release Recommendation

**READY WITH KNOWN ISSUES** — 최초 발견한 P1 두 건은 해결됐고 전체 회귀와 실제 외부 연동을
통과했다. 남은 P2 네 건은 안전한 차단 또는 UX·접근성·검색 범위 문제이며 핵심 계산값과 민감정보
보호를 훼손하지 않는다.
