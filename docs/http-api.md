# MarginCast HTTP API

`src.http_api`는 MarginCast 계산 엔진과 정적 웹사이트를 Python 표준 라이브러리 HTTP 서버로
제공한다. Agent나 LLM을 실행하지 않으며 `src.agent_tool_contracts`의 검증된 도구 계약을
그대로 호출한다.

## 실행

```powershell
.\.venv\Scripts\python.exe -m src.http_api
```

기본 주소는 `http://127.0.0.1:8000`이다. 같은 서버에서 웹사이트와 `/api/*` JSON API를
제공하므로 별도 CORS 설정이 필요하지 않다.

전략 응답의 `data_provenance`는 현재 데이터가 `synthetic-pos-v2`이며 실제 매장 데이터가
아님을 명시한다. `evidence_quality`는 성공확률과 다른 `heuristic-v1` 근거 품질 점수이고,
실제 매장 결과와의 관계가 아직 검증되지 않았다. 산식과 해석은
[Decision Engine 데이터 출처와 판단 방법](decision-methodology.md)을 참고한다.

## 경로

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/health` | 서버 버전과 실행 상태 |
| `GET` | `/api/capabilities` | 지원 메뉴, 데이터 범위, 입력 한도 |
| `POST` | `/api/strategies/price` | 가격·할인 전략 비교 |
| `POST` | `/api/strategies/price/forecast` | 매장 주소의 실제 단기예보를 반영한 가격·할인 비교 |
| `POST` | `/api/strategies/bundle` | 세트 전략 시뮬레이션 |
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

도메인 검증 실패도 JSON 오류 객체로 반환한다. 잘못된 입력은 `400`, 없는 경로는 `404`,
분석 패널이 준비되지 않은 경우는 `503`을 사용한다. 요청 본문은 64KiB로 제한한다.

## 배포 전 남은 항목

현재 서버 기본값은 로컬 접근만 허용한다. 공개 배포에서는 앞단 프록시의 TLS, 요청 제한,
접근 로그와 운영 오류 수집을 추가해야 한다. 브라우저에 제공하는 파일에는 기상청·카카오
서버 키를 포함하지 않는다.

실험 피드백은 반드시 계획을 먼저 저장한 뒤 해당 `feedback_id`에 실제 결과를 연결한다.
계획에는 당시 엔진·데이터·날씨·근거 품질 버전을 함께 저장한다.
요청 형식과 보정 지표는 [실험 피드백과 모델 보정 근거](experiment-feedback.md)에 정리했다.
