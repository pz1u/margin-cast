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

## 현재 구현: 90일 Synthetic POS Dataset

8개 메뉴의 가격·할인·날씨·요일·시간대·원가 변화와 세트 전환을 반영합니다.
생성기와 검증기는 Python 표준 라이브러리만 사용합니다.

프로젝트 루트의 PowerShell에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m src.generate_data --seed 42
.\.venv\Scripts\python.exe -m src.validate_data --check-reproducibility
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

기본 기간은 2026-01-05~2026-04-04이며 생성할 때 기존 출력 파일을 갱신합니다.
시작일과 저장 폴더는 `--start-date YYYY-MM-DD`, `--output-dir 경로`로 변경할 수 있습니다.
별도 폴더를 검증할 때는 `--data-dir 경로`를 지정합니다.
데이터를 다시 생성했다면 검증도 다시 실행해 `validation_report.json`을 갱신합니다.

기본 seed 결과: 주문 **14,676건**, 주문상품 **20,676건**, 테스트 **19개 통과**.
관측 데이터·실험 정의는 CSV 8개, 숨겨진 생성 파라미터와 대조 결과는
`data/ground_truth.json`, 검증 결과는 `data/validation_report.json`에 저장됩니다.
생성 CSV/JSON은 기존 `.gitignore` 정책에 따라 Git에서 제외합니다.

데이터 사전, 수식, 실제 검증 수치, 모델링 한계는
[합성 데이터 설계와 검증](docs/synthetic-data.md)에 정리했습니다.

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

Day 1~60을 학습하고 Validation 15일, Test 15일을 시간순으로 평가합니다.

```powershell
.\.venv\Scripts\python.exe -m src.train_demand_model
```

0판매 셀을 포함한 Poisson 회귀 결과와 과거 조건부 평균 기준선 비교는
[수요 기준 모델 평가](reports/modeling/baseline/README.md)에 저장됩니다.

## 가격탄력성

학습 구간에 포함된 가격 실험으로 치킨마요의 가격탄력성을 추정합니다.

```powershell
.\.venv\Scripts\python.exe -m src.estimate_elasticity
```

추정값과 가격별 수요 배수는
[가격탄력성 보고서](reports/modeling/elasticity/README.md)에서 확인할 수 있습니다.

## 가격·할인 시뮬레이션

기준 수요 모델과 탄력성 추정치를 연결해 14일 전략별 판매량·기여이익 분포를 계산합니다.

```powershell
.\.venv\Scripts\python.exe -m src.simulate_strategy
```

비교 결과와 해석 한계는
[Monte Carlo 시뮬레이션 보고서](reports/simulation/README.md)에 저장됩니다.
