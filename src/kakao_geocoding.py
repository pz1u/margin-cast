"""카카오 로컬 REST API로 매장 주소를 WGS84 좌표로 변환한다."""

import os
from pathlib import Path

import requests

from src.env_config import read_env_value


API_URL = "https://dapi.kakao.com/v2/local/search/address.json"
REST_API_KEY_NAME = "KAKAO_REST_API_KEY"


class KakaoConfigurationError(ValueError):
    """카카오 로컬 API 설정이 없거나 잘못된 경우."""


class KakaoGeocodingError(RuntimeError):
    """카카오 로컬 API가 주소 좌표를 반환하지 못한 경우."""


def get_rest_api_key(rest_api_key=None, env_path=None):
    """명시 인자, 운영체제 환경변수, 프로젝트 .env 순서로 키를 찾는다."""
    if rest_api_key and rest_api_key.strip():
        return rest_api_key.strip()

    environment_value = os.environ.get(REST_API_KEY_NAME, "").strip()
    if environment_value:
        return environment_value

    if env_path is None:
        env_path = Path(__file__).resolve().parents[1] / ".env"
    file_value = read_env_value(env_path, REST_API_KEY_NAME)
    if file_value:
        return file_value

    raise KakaoConfigurationError(
        f"{REST_API_KEY_NAME}가 없습니다. 프로젝트 .env 또는 운영체제 환경변수에 입력하세요."
    )


def geocode_address(
    address,
    *,
    rest_api_key=None,
    env_path=None,
    timeout=15,
    session=None,
):
    """주소를 검색해 시뮬레이션에 필요한 정규화 주소와 위경도를 반환한다."""
    address = str(address or "").strip()
    if not address:
        raise KakaoConfigurationError("검색할 매장 주소를 입력하세요.")

    rest_api_key = get_rest_api_key(rest_api_key, env_path)
    client = session or requests
    try:
        response = client.get(
            API_URL,
            headers={"Authorization": f"KakaoAK {rest_api_key}"},
            params={"query": address, "size": 1},
            timeout=timeout,
        )
        payload = response.json()
    except requests.RequestException:
        raise KakaoGeocodingError(
            "카카오 주소 검색 중 네트워크 또는 HTTP 오류가 발생했습니다."
        ) from None
    except ValueError:
        raise KakaoGeocodingError(
            "카카오 주소 검색이 JSON이 아닌 응답을 반환했습니다."
        ) from None

    if response.status_code >= 400:
        if isinstance(payload, dict):
            error_type = payload.get("errorType", "API 오류")
            message = payload.get("message", "설정을 확인하세요.")
        else:
            error_type = "API 오류"
            message = "설정을 확인하세요."
        raise KakaoGeocodingError(
            f"카카오 주소 검색 오류 {response.status_code} ({error_type}): {message}"
        )

    documents = payload.get("documents", []) if isinstance(payload, dict) else []
    if not documents:
        raise KakaoGeocodingError(f"주소 검색 결과가 없습니다: {address}")

    document = documents[0]
    try:
        latitude = float(document["y"])
        longitude = float(document["x"])
    except (KeyError, TypeError, ValueError) as error:
        raise KakaoGeocodingError("카카오 주소 검색 결과에 유효한 위경도가 없습니다.") from error

    road_address = document.get("road_address") or {}
    normalized_address = road_address.get("address_name") or document.get("address_name")
    return {
        "query": address,
        "address_name": normalized_address,
        "latitude": latitude,
        "longitude": longitude,
    }
