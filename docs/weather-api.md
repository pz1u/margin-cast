# 기상청 단기예보 연동

`src.weather_forecast`는 공공데이터포털 또는 기상청 API허브의 동네예보 단기예보 JSON 응답을
MarginCast 시뮬레이션에서 사용할 시간별 문맥으로 변환한다.

## 인증키 설정

프로젝트 루트에서 예시 파일을 복사한다.

```powershell
Copy-Item .env.example .env
```

`.env`의 값만 채운다. 이 파일은 Git에서 제외된다.

```dotenv
KMA_SERVICE_KEY=발급받은_공공데이터포털_또는_API허브_인증키
KMA_API_PROVIDER=auto
```

운영체제 환경변수 `KMA_SERVICE_KEY`도 사용할 수 있으며, `.env`보다 우선한다.
`auto`는 `%` 인코딩된 긴 공공데이터포털 키와 API허브 키를 구분한다. 필요하면
`data_go` 또는 `api_hub`를 명시할 수 있다.

## 실행

매장 위도·경도를 전달하면 내부에서 기상청 동네예보 격자로 변환한다.

```powershell
.\.venv\Scripts\python.exe -m src.weather_forecast `
  --latitude 37.5665 `
  --longitude 126.9780
```

이미 격자를 알고 있으면 `nx`, `ny`를 직접 전달할 수도 있다.

```powershell
.\.venv\Scripts\python.exe -m src.weather_forecast --nx 60 --ny 127
```

위경도는 WGS84 좌표를 사용한다. 변환은 기상청 공식 Lambert 격자 사양을 프로젝트 안에서
계산하므로 별도의 위치 API나 인증키가 필요하지 않다. 주소 문자열만 알고 있는 경우의 주소→위경도
변환은 사용자 주소 입력 화면이 생기는 Web 단계에서 지오코딩 공급자를 정해 연결한다.

결과는 기본적으로 `data/processed/weather_forecast.json`에 저장된다. 각 예보시각에는
기온 `tmp_c`, 강수량 원문 `pcp_raw`, 계산용 강수량 대표값 `pcp_mm_estimate`,
연장기간 정성 강수코드 `pcp_category_code`, 강수형태 `pty_code`, 습도 `reh_pct`,
강수확률 `pop_pct`, 하늘상태 `sky_code`,
비 여부 `is_rain`이 포함된다.

`PCP`가 범주형 문자열이면 계산을 위해 대표값을 사용한다. 예를 들어 `1.0mm 미만`은
0.5mm, `30.0~50.0mm`는 40mm로 변환하며 API 원문도 함께 보존한다.
연장기간에 숫자만 오는 정성 강수코드는 mm로 환산하지 않고 별도 코드로 보존한다.

생성한 파일은 가격·할인 시뮬레이션의 미래 문맥으로 전달할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m src.simulate_strategy `
  --horizon-days 4 `
  --weather-forecast data\processed\weather_forecast.json
```

엔진은 예보 날짜의 11~21시 각 영업시간에 가장 가까운 예보값을 연결한다. 요청한 기간보다
미래 예보 날짜가 적으면 관측 날씨를 반복하지 않고 오류를 반환한다.

## 범위와 한계

현재 연동은 단기예보가 제공하는 기간까지만 가져온다. 기상청은 단기예보 기간을 최대
5일까지 제공하므로 7일 문맥 전체가 필요하면 4~7일 중기예보를 별도로 결합해야 한다.
중기예보는 단기예보와 시간 해상도와 제공 변수가 달라 동일한 방식으로 채우면 안 된다.

인증키가 없는 개발 환경에서는 실호출하지 않고 고정 응답을 이용한 파서 테스트를 실행한다.

공식 형식은 [기상청 API허브 단기예보](https://apihub.kma.go.kr/apiList.do?seqApi=10),
예보기간은 [단기예보 기간 확대 안내](https://apihub.kma.go.kr/notice.do?seqNotice=33),
좌표 변환은 [동네예보 격자영역 정보](https://apihub.kma.go.kr/getAttachFile.do?fileName=%2820240305%29%EB%8F%99%EB%84%A4%EC%98%88%EB%B3%B4+%EA%B2%A9%EC%9E%90%EC%98%81%EC%97%AD+%EC%A0%95%EB%B3%B4.pdf)를 기준으로 했다.
