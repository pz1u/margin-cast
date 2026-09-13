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