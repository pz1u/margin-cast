# 기상청 단기예보 연동

`src.weather_forecast`는 기상청 API허브의 동네예보 단기예보 JSON 응답을
MarginCast 시뮬레이션에서 사용할 시간별 문맥으로 변환한다.

## 인증키 설정

프로젝트 루트에서 예시 파일을 복사한다.

```powershell
Copy-Item .env.example .env
```

`.env`의 값만 채운다. 이 파일은 Git에서 제외된다.

```dotenv
KMA_SERVICE_KEY=발급받은_기상청_API허브_인증키
```

운영체제 환경변수 `KMA_SERVICE_KEY`도 사용할 수 있으며, `.env`보다 우선한다.

## 실행

매장 주소에 대응하는 동네예보 격자 좌표를 전달한다.

```powershell
.\.venv\Scripts\python.exe -m src.weather_forecast --nx 60 --ny 127
```

결과는 기본적으로 `data/processed/weather_forecast.json`에 저장된다. 각 예보시각에는
기온 `tmp_c`, 강수량 원문 `pcp_raw`, 계산용 강수량 대표값 `pcp_mm_estimate`,
연장기간 정성 강수코드 `pcp_category_code`, 강수형태 `pty_code`, 습도 `reh_pct`,
강수확률 `pop_pct`, 하늘상태 `sky_code`,
비 여부 `is_rain`이 포함된다.

`PCP`가 범주형 문자열이면 계산을 위해 대표값을 사용한다. 예를 들어 `1.0mm 미만`은
0.5mm, `30.0~50.0mm`는 40mm로 변환하며 API 원문도 함께 보존한다.
연장기간에 숫자만 오는 정성 강수코드는 mm로 환산하지 않고 별도 코드로 보존한다.

## 범위와 한계

현재 연동은 단기예보가 제공하는 기간까지만 가져온다. 기상청은 단기예보 기간을 최대
5일까지 제공하므로 7일 문맥 전체가 필요하면 4~7일 중기예보를 별도로 결합해야 한다.
중기예보는 단기예보와 시간 해상도와 제공 변수가 달라 동일한 방식으로 채우면 안 된다.

인증키가 없는 개발 환경에서는 실호출하지 않고 고정 응답을 이용한 파서 테스트를 실행한다.

공식 형식은 [기상청 API허브 단기예보](https://apihub.kma.go.kr/apiList.do?seqApi=10),
예보기간은 [단기예보 기간 확대 안내](https://apihub.kma.go.kr/notice.do?seqNotice=33)를 기준으로 했다.
