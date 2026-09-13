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
현재 단계는 데이터 생성과 검증까지이며, 다음 단계는 EDA입니다.
