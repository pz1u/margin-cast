"""매장 위도·경도를 기상청 동네예보 격자로 변환한다."""

import math


EARTH_RADIUS_KM = 6371.00877
GRID_SPACING_KM = 5.0
STANDARD_LATITUDE_1 = 30.0
STANDARD_LATITUDE_2 = 60.0
ORIGIN_LONGITUDE = 126.0
ORIGIN_LATITUDE = 38.0
ORIGIN_X = 43.0
ORIGIN_Y = 136.0
GRID_X_RANGE = range(1, 150)
GRID_Y_RANGE = range(1, 254)


class KmaGridError(ValueError):
    """좌표가 잘못됐거나 동네예보 격자 범위를 벗어난 경우."""


def latlon_to_grid(latitude, longitude):
    """WGS84 위도·경도를 기상청 Lambert 격자 ``(nx, ny)``로 변환한다."""
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError) as error:
        raise KmaGridError("위도와 경도는 숫자여야 합니다.") from error

    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise KmaGridError("위도와 경도는 유한한 숫자여야 합니다.")
    if not -90 < latitude < 90 or not -180 <= longitude <= 180:
        raise KmaGridError("위도는 -90 초과 90 미만, 경도는 -180~180 범위여야 합니다.")

    degree_to_radian = math.pi / 180.0
    earth_radius = EARTH_RADIUS_KM / GRID_SPACING_KM
    standard_latitude_1 = STANDARD_LATITUDE_1 * degree_to_radian
    standard_latitude_2 = STANDARD_LATITUDE_2 * degree_to_radian
    origin_longitude = ORIGIN_LONGITUDE * degree_to_radian
    origin_latitude = ORIGIN_LATITUDE * degree_to_radian

    cone = math.tan(math.pi * 0.25 + standard_latitude_2 * 0.5)
    cone /= math.tan(math.pi * 0.25 + standard_latitude_1 * 0.5)
    cone = math.log(math.cos(standard_latitude_1) / math.cos(standard_latitude_2)) / math.log(cone)

    scale = math.tan(math.pi * 0.25 + standard_latitude_1 * 0.5)
    scale = (scale**cone) * math.cos(standard_latitude_1) / cone
    origin_radius = math.tan(math.pi * 0.25 + origin_latitude * 0.5)
    origin_radius = earth_radius * scale / (origin_radius**cone)

    radius = math.tan(math.pi * 0.25 + latitude * degree_to_radian * 0.5)
    radius = earth_radius * scale / (radius**cone)
    theta = longitude * degree_to_radian - origin_longitude
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= cone

    nx = int(radius * math.sin(theta) + ORIGIN_X + 0.5)
    ny = int(origin_radius - radius * math.cos(theta) + ORIGIN_Y + 0.5)
    if nx not in GRID_X_RANGE or ny not in GRID_Y_RANGE:
        raise KmaGridError(
            "입력한 위치가 기상청 동네예보 격자 범위를 벗어났습니다. "
            f"계산 결과: nx={nx}, ny={ny}"
        )
    return nx, ny


def resolve_grid_coordinates(*, nx=None, ny=None, latitude=None, longitude=None):
    """격자 한 쌍 또는 위경도 한 쌍을 검증해 동네예보 격자를 반환한다."""
    has_any_grid = nx is not None or ny is not None
    has_any_latlon = latitude is not None or longitude is not None
    if has_any_grid and has_any_latlon:
        raise KmaGridError("nx·ny와 위도·경도 중 한 방식만 입력하세요.")
    if has_any_grid:
        if nx is None or ny is None:
            raise KmaGridError("nx와 ny를 함께 입력하세요.")
        nx, ny = int(nx), int(ny)
        if nx not in GRID_X_RANGE or ny not in GRID_Y_RANGE:
            raise KmaGridError("격자 좌표는 nx=1~149, ny=1~253 범위여야 합니다.")
        return nx, ny
    if has_any_latlon:
        if latitude is None or longitude is None:
            raise KmaGridError("위도와 경도를 함께 입력하세요.")
        return latlon_to_grid(latitude, longitude)
    raise KmaGridError("nx·ny 또는 위도·경도를 입력하세요.")
