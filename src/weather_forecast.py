"""기상청 단기예보를 MarginCast 시뮬레이션 문맥으로 변환한다."""

import argparse
import json
import os
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import requests

from src.env_config import read_env_value
from src.kakao_geocoding import (
    KakaoConfigurationError,
    KakaoGeocodingError,
    geocode_address,
)
from src.location_grid import KmaGridError, latlon_to_grid, resolve_grid_coordinates


KST = timezone(timedelta(hours=9))
BASE_TIMES = (2, 5, 8, 11, 14, 17, 20, 23)
API_HUB_URL = (
    "https://apihub.kma.go.kr/api/typ02/openApi/"
    "VilageFcstInfoService_2.0/getVilageFcst"
)
DATA_GO_URL = (
    "https://apis.data.go.kr/1360000/"
    "VilageFcstInfoService_2.0/getVilageFcst"
)
API_URL = DATA_GO_URL
SERVICE_KEY_NAME = "KMA_SERVICE_KEY"
PROVIDER_NAME = "KMA_API_PROVIDER"
SIMULATION_CATEGORIES = {"TMP", "PCP", "PTY", "REH", "POP", "SKY"}


class KmaConfigurationError(ValueError):
    """기상청 API 호출 설정이 없거나 잘못된 경우."""


class KmaApiError(RuntimeError):
    """기상청 API가 정상 예보를 반환하지 않은 경우."""


def get_service_key(service_key=None, env_path=None):
    """명시 인자, 운영체제 환경변수, 프로젝트 .env 순서로 키를 찾는다."""
    if service_key and service_key.strip():
        return service_key.strip()

    environment_value = os.environ.get(SERVICE_KEY_NAME, "").strip()
    if environment_value:
        return environment_value

    if env_path is None:
        env_path = Path(__file__).resolve().parents[1] / ".env"
    file_value = read_env_value(env_path, SERVICE_KEY_NAME)
    if file_value:
        return file_value

    raise KmaConfigurationError(
        f"{SERVICE_KEY_NAME}가 없습니다. 프로젝트 .env 또는 운영체제 환경변수에 입력하세요."
    )


def resolve_provider(service_key, provider=None, env_path=None):
    """명시 설정을 우선하고 인코딩된 공공데이터포털 키는 자동 판별한다."""
    if provider is None:
        provider = os.environ.get(PROVIDER_NAME)
    if provider is None:
        env_path = env_path or Path(__file__).resolve().parents[1] / ".env"
        provider = read_env_value(env_path, PROVIDER_NAME)
    provider = (provider or "auto").strip().lower()
    if provider not in {"auto", "data_go", "api_hub"}:
        raise KmaConfigurationError(
            f"{PROVIDER_NAME}는 auto, data_go, api_hub 중 하나여야 합니다."
        )
    if provider == "auto":
        provider = "data_go" if "%" in service_key or len(service_key) > 64 else "api_hub"
    return provider


def latest_available_base(now=None, publication_delay_minutes=15):
    """KST 기준으로 응답이 공개됐을 가능성이 있는 최근 발표시각을 고른다."""
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)

    for day_offset in (0, -1):
        candidate_date = (now + timedelta(days=day_offset)).date()
        for hour in reversed(BASE_TIMES):
            candidate = datetime.combine(candidate_date, time(hour), tzinfo=KST)
            if candidate + timedelta(minutes=publication_delay_minutes) <= now:
                return candidate
    raise RuntimeError("기상청 단기예보 발표시각을 계산하지 못했습니다.")


def _number(value, cast=float):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def precipitation_mm_estimate(value):
    """범주형 PCP를 계산 가능한 대표값으로 바꾸고 원문은 별도로 보존한다."""
    text = str(value).strip()
    if not text or text in {"-", "강수없음", "0", "0.0"}:
        return 0.0
    # 연장 예보기간의 PCP는 mm가 아니라 정성 코드로 올 수 있어 수치 강수량으로 오인하지 않는다.
    if text in {"1", "2", "3", "4"}:
        return None
    numbers = [float(number) for number in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    if "미만" in text:
        return numbers[0] / 2
    if "~" in text and len(numbers) >= 2:
        return sum(numbers[:2]) / 2
    return numbers[0]


def parse_forecast_response(payload):
    """기상청 JSON 응답을 예보시각별 시뮬레이션 문맥으로 정규화한다."""
    try:
        response = payload["response"]
        header = response["header"]
    except (KeyError, TypeError) as error:
        raise KmaApiError("기상청 응답 형식을 해석할 수 없습니다.") from error

    result_code = str(header.get("resultCode", ""))
    if result_code != "00":
        message = header.get("resultMsg", "알 수 없는 오류")
        raise KmaApiError(f"기상청 API 오류 {result_code}: {message}")

    body = response.get("body") or {}
    items_container = body.get("items") or {}
    items = items_container.get("item") or []
    if isinstance(items, dict):
        items = [items]

    grouped = {}
    for item in items:
        category = item.get("category")
        if category not in SIMULATION_CATEGORIES:
            continue
        key = (str(item.get("fcstDate", "")), str(item.get("fcstTime", "")).zfill(4))
        if len(key[0]) != 8 or len(key[1]) != 4:
            continue
        row = grouped.setdefault(
            key,
            {
                "forecast_at": datetime.strptime("".join(key), "%Y%m%d%H%M")
                .replace(tzinfo=KST)
                .isoformat(),
                "date": f"{key[0][:4]}-{key[0][4:6]}-{key[0][6:]}",
                "time": f"{key[1][:2]}:{key[1][2:]}",
                "nx": _number(item.get("nx"), int),
                "ny": _number(item.get("ny"), int),
            },
        )
        row[category] = item.get("fcstValue")

    forecasts = []
    for row in grouped.values():
        pcp_raw = row.pop("PCP", None)
        pcp_estimate = precipitation_mm_estimate(pcp_raw)
        pcp_category_code = (
            int(pcp_raw) if str(pcp_raw).strip() in {"1", "2", "3", "4"} else None
        )
        pty = _number(row.pop("PTY", None), int)
        tmp = _number(row.pop("TMP", None))
        reh = _number(row.pop("REH", None))
        pop = _number(row.pop("POP", None))
        sky = _number(row.pop("SKY", None), int)
        forecasts.append(
            {
                **row,
                "tmp_c": tmp,
                "pcp_raw": pcp_raw,
                "pcp_mm_estimate": pcp_estimate,
                "pcp_category_code": pcp_category_code,
                "pty_code": pty,
                "reh_pct": reh,
                "pop_pct": pop,
                "sky_code": sky,
                "is_rain": bool(
                    (pty or 0) > 0 or (pcp_estimate or 0) > 0 or pcp_category_code
                ),
            }
        )
    return sorted(forecasts, key=lambda row: row["forecast_at"])


def fetch_village_forecast(
    nx,
    ny,
    *,
    service_key=None,
    env_path=None,
    base_datetime=None,
    timeout=15,
    session=None,
    provider=None,
):
    """기상청 단기예보를 호출하고 정규화된 시간별 예보를 반환한다."""
    nx, ny = int(nx), int(ny)
    if not 1 <= nx <= 149 or not 1 <= ny <= 253:
        raise KmaConfigurationError("격자 좌표는 nx=1~149, ny=1~253 범위여야 합니다.")

    base_datetime = base_datetime or latest_available_base()
    if base_datetime.tzinfo is None:
        base_datetime = base_datetime.replace(tzinfo=KST)
    base_datetime = base_datetime.astimezone(KST)
    service_key = get_service_key(service_key, env_path)
    provider = resolve_provider(service_key, provider, env_path)
    params = {
        "pageNo": 1,
        "numOfRows": 1000,
        "dataType": "JSON",
        "base_date": base_datetime.strftime("%Y%m%d"),
        "base_time": base_datetime.strftime("%H%M"),
        "nx": nx,
        "ny": ny,
    }
    if provider == "data_go":
        url = DATA_GO_URL
        # requests가 쿼리를 한 번 인코딩하므로 포털의 인코딩 키는 먼저 복원한다.
        params["serviceKey"] = unquote(service_key)
    else:
        url = API_HUB_URL
        params["authKey"] = service_key
    client = session or requests
    try:
        response = client.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        # HTTP 예외 문자열에는 인증키가 포함된 최종 URL이 들어갈 수 있어 그대로 노출하지 않는다.
        raise KmaApiError("기상청 API 호출 중 네트워크 또는 HTTP 오류가 발생했습니다.") from None
    except ValueError:
        raise KmaApiError("기상청 API가 JSON이 아닌 응답을 반환했습니다.") from None
    return parse_forecast_response(payload)


def resolve_forecast_location(
    *,
    address=None,
    nx=None,
    ny=None,
    latitude=None,
    longitude=None,
    env_path=None,
    geocoder=geocode_address,
):
    """주소, 위경도 또는 격자를 하나의 기상청 격자 입력으로 정규화한다."""
    if address:
        if any(value is not None for value in (nx, ny, latitude, longitude)):
            raise KmaConfigurationError(
                "주소와 nx·ny 또는 위도·경도를 함께 입력할 수 없습니다."
            )
        location = geocoder(address, env_path=env_path)
        resolved_nx, resolved_ny = latlon_to_grid(
            location["latitude"], location["longitude"]
        )
        return resolved_nx, resolved_ny, location

    resolved_nx, resolved_ny = resolve_grid_coordinates(
        nx=nx,
        ny=ny,
        latitude=latitude,
        longitude=longitude,
    )
    return resolved_nx, resolved_ny, None


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nx", type=int, help="동네예보 격자 X 좌표")
    parser.add_argument("--ny", type=int, help="동네예보 격자 Y 좌표")
    parser.add_argument("--latitude", type=float, help="매장 위도(WGS84)")
    parser.add_argument("--longitude", type=float, help="매장 경도(WGS84)")
    parser.add_argument("--address", help="카카오 로컬 API로 검색할 매장 도로명 또는 지번 주소")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "data" / "processed" / "weather_forecast.json",
    )
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--provider", choices=["auto", "data_go", "api_hub"], default="auto")
    args = parser.parse_args()

    try:
        nx, ny, location = resolve_forecast_location(
            address=args.address,
            nx=args.nx,
            ny=args.ny,
            latitude=args.latitude,
            longitude=args.longitude,
            env_path=args.env_file,
        )
    except (KmaGridError, KakaoConfigurationError, KakaoGeocodingError) as error:
        parser.error(str(error))

    forecasts = fetch_village_forecast(
        nx, ny, env_path=args.env_file, provider=args.provider
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(forecasts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "rows": len(forecasts),
                "nx": nx,
                "ny": ny,
                "location_source": "kakao_address" if location else "coordinates",
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
