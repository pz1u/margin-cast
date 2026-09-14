# MarginCast 개발 로드맵

## 현재 공식 상태

- Phase 1 환경 안정화: 완료
- Phase 2 Synthetic Dataset v2: 완료
- Phase 3 EDA 재실행 및 Feature Dataset 갱신: 완료
- Phase 4 Baseline·가격탄력성·할인 효과: 완료
- Phase 5 기상청 API: 카카오 주소 검색·매장 위경도 변환·공공데이터포털 실호출·Simulation 연결 완료, 동일 해상도 7일 결합 대기
- Phase 6 Price Simulation: 완료
- Phase 7 Monte Carlo·Confidence: 완료
- Phase 8 할인·세트 Simulation: 완료
- Phase 9 Decision Engine: 완료
- Phase 10 Agent: 도구 등록 직전 준비 완료, 사용자 주도 구현 대기

v1 데이터로 만든 시뮬레이션·도구 계약은 전체 연결을 검증한 프로토타입이다.
Phase 3~4 결과는 v2 데이터로 다시 실행해 갱신했고 이후 단계도 같은 기준으로 검증한다.

## 진행 순서

1. 환경 안정화
2. Synthetic Dataset v2
3. EDA
4. Feature Dataset
5. Baseline Demand
6. 가격탄력성
7. 할인효과
8. 기상청 API
9. Price Simulation
10. Monte Carlo
11. Confidence
12. 할인·세트 Simulation
13. Decision Engine
14. Agent
15. Web

## Phase별 완료 조건

| Phase | 작업 | 완료 조건 |
|---|---|---|
| 1 | 분석 환경 안정화 | 데이터 생성·EDA·전체 테스트 통과 |
| 2 | 180일 v2 데이터 | 반복 가격·할인 실험, 다중 메뉴, 세트 분해 Ground Truth 검증 |
| 3 | EDA·Feature Dataset | 0판매 셀 포함, 개입별 관측값과 누수 방지 계약 확인 |
| 4 | Baseline·탄력성·할인 | 120/30/30 시간순 평가, Ground Truth 대비 오차와 불확실성 제시 |
| 5 | 기상청 API | 미래 날짜별 날씨 예보를 모델 입력으로 변환 |
| 6~7 | Price·Monte Carlo·Confidence | 기대값, 범위, 성공확률, 신뢰도를 분리해 반환 |
| 8 | 할인·세트 | 할인 효과와 세트 신규 수요·잠식을 구분 |
| 9 | Decision Engine | 기대이익·성공확률·하방 위험·근거 수준으로 비교 |
| 10 | Agent | 계산값을 만들지 않고 도구 호출과 설명만 담당 |
| 11 | Web·API | 사용자 흐름, 배포, 모델 보정 경로 검증 |

## 역할 경계

핵심 수치는 통계 모델과 시뮬레이션이 계산한다. LLM은 전략 후보 생성, 도구 호출,
결과 비교와 사용자 친화적 설명을 담당한다. Agent의 프롬프트와 런타임 구현은 사용자가 주도한다.
