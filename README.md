# MarginCast

AI 기반 소상공인 경영 의사결정 시뮬레이터

## Overview

MarginCast는 음식점의 주문·가격·원가·프로모션 데이터를 분석해
가격 변경, 할인 변경, 세트 구성 등의 경영 전략을 사전에 시뮬레이션하고
예상 판매량과 기대 이익을 제공하는 서비스입니다.

## MVP

- Synthetic POS Dataset 생성
- 메뉴별 수요 예측
- 가격탄력성 추정
- 가격 변경 What-if Simulation
- 할인 변경 Simulation
- 세트메뉴 Simulation
- Monte Carlo 기반 기대 이익 및 성공 확률 계산

## Tech

- Python
- Pandas
- NumPy
- scikit-learn
- statsmodels
- Jupyter Notebook

## Structure

```text
margin-cast/
├── data/
├── notebooks/
├── src/
├── requirements.txt
└── README.md
```

## 현재 구현: 180일 Synthetic POS Dataset v2

8개 메뉴의 가격·할인·날씨·요일·시간대·원가 변화와 세트 전환을 반영합니다.
생성기와 검증기는 Python 표준 라이브러리만 사용합니다.

프로젝트 루트의 PowerShell에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m src.generate_data --seed 42
.\.venv\Scripts\python.exe -m src.validate_data --check-reproducibility
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

기본 기간은 2026-01-05~2026-07-03이며 생성할 때 기존 출력 파일을 갱신합니다.
시작일과 저장 폴더는 `--start-date YYYY-MM-DD`, `--output-dir 경로`로 변경할 수 있습니다.
별도 폴더를 검증할 때는 `--data-dir 경로`를 지정합니다.
데이터를 다시 생성했다면 검증도 다시 실행해 `validation_report.json`을 갱신합니다.

기본 seed 결과: 주문 **29,473건**, 주문상품 **41,510건**입니다.
M01·M02·M03 가격 실험 11개, M01·M02 할인 실험 5개, 세트 실험 1개를 포함합니다.
관측 데이터·실험 정의는 CSV 8개, 숨겨진 생성 파라미터와 대조 결과는
`data/ground_truth.json`, 검증 결과는 `data/validation_report.json`에 저장됩니다.
생성 CSV/JSON은 기존 `.gitignore` 정책에 따라 Git에서 제외합니다.

데이터 사전, 수식, 실제 검증 수치, 모델링 한계는
[합성 데이터 설계와 검증](docs/synthetic-data.md)에 정리했습니다.
전체 진행 순서와 공식 완료 상태는 [개발 로드맵](docs/roadmap.md)을 기준으로 합니다.

## EDA와 학습용 패널

관측 가능한 CSV만 사용해 날짜×시간×채널×메뉴 패널을 만들고 EDA 보고서를 생성합니다.

```powershell
.\.venv\Scripts\python.exe -m src.prepare_analysis_data
.\.venv\Scripts\python.exe -m src.run_eda
```

학습 패널은 `data/processed/demand_panel.csv`, 검증 결과는 같은 폴더의
`panel_validation.json`에 생성되며 Git에서 제외됩니다.
EDA 결과는 [관측 데이터 EDA](reports/eda/README.md)에서 확인할 수 있습니다.

## 수요 기준 모델

Day 1~120을 학습하고 Validation 30일, Test 30일을 시간순으로 평가합니다.

```powershell
.\.venv\Scripts\python.exe -m src.train_demand_model
```

0판매 셀을 포함한 Poisson 회귀 결과와 과거 조건부 평균 기준선 비교는
[수요 기준 모델 평가](reports/modeling/baseline/README.md)에 저장됩니다.

## 가격탄력성

학습 구간에 포함된 반복 가격·할인 실험으로 M01·M02·M03의 가격탄력성과
M01·M02의 가격 외 할인 노출 효과를 분리해 추정합니다.

```powershell
.\.venv\Scripts\python.exe -m src.estimate_elasticity
.\.venv\Scripts\python.exe -m src.evaluate_effect_models
```

추정값과 가격별 수요 배수는
[가격탄력성 보고서](reports/modeling/elasticity/README.md)와
[Ground Truth 대비 효과 평가](reports/modeling/effects/README.md)에서 확인할 수 있습니다.

## 기상청 단기예보

기상청 인증키와 매장 주소를 입력하면 카카오 로컬 API로 위경도를 찾고,
TMP·PCP·PTY·REH·POP·SKY를
시뮬레이션용 시간별 문맥으로 변환합니다.

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m src.weather_forecast `
  --address "서울특별시 중구 세종대로 110"
.\.venv\Scripts\python.exe -m src.simulate_strategy `
  --horizon-days 4 `
  --weather-forecast data\processed\weather_forecast.json
```

시뮬레이션은 요청 일수보다 실제 미래 예보가 짧으면 최근 날씨로 채우지 않고 오류를 반환합니다.
설정과 예보 범위의 한계는 [기상청 단기예보 연동](docs/weather-api.md)을 참고하세요.

## 가격·할인 시뮬레이션

기준 수요 모델과 탄력성 추정치를 연결해 14일 전략별 판매량·기여이익 분포를 계산합니다.
수요·탄력성·할인효과·원가 불확실성을 반영하고 80% 예상 범위, 개선확률, 근거 품질
신뢰도(`HIGH`/`MEDIUM`/`LOW`)를 서로 분리해 반환합니다.

```powershell
.\.venv\Scripts\python.exe -m src.simulate_strategy
```

비교 결과와 해석 한계는
[Monte Carlo 시뮬레이션 보고서](reports/simulation/README.md)에 저장됩니다.

세트는 관측 장바구니 기회와 사용자가 입력하는 Take Rate·신규 수요율·잠식률을 분리해
계산합니다. POS만으로 세 값이 식별되지 않으므로 기본 결과의 신뢰도는 `LOW`입니다.

```powershell
.\.venv\Scripts\python.exe -m src.simulate_bundle
```

결과는 [세트 전략 시뮬레이션 보고서](reports/simulation/bundle/README.md)에 저장됩니다.

## AI 에이전트 연동 준비

AI 에이전트가 계산 결과를 직접 생성하지 않고 검증된 엔진을 호출하도록 서비스 경계와
함수 도구 스키마를 준비했습니다. 실제 에이전트 등록과 프롬프트 구현은 포함하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m src.agent_tool_contracts get_margincast_capabilities
.\.venv\Scripts\python.exe -m src.agent_tool_contracts compare_price_strategies `
  --arguments-file examples\compare-price-strategies.json
.\.venv\Scripts\python.exe -m src.agent_tool_contracts simulate_bundle_strategy `
  --arguments-file examples\simulate-bundle-strategy.json
```

연동 규칙과 남은 작업은 [에이전트 도구 연동 계약](docs/agent-tool-contract.md)을 참고하세요.
