import unittest

from src.location_grid import KmaGridError, latlon_to_grid, resolve_grid_coordinates


class LocationGridTests(unittest.TestCase):
    def test_projection_origin_maps_to_official_origin_grid(self):
        self.assertEqual(latlon_to_grid(38.0, 126.0), (43, 136))

    def test_seoul_city_hall_maps_to_known_village_forecast_grid(self):
        self.assertEqual(latlon_to_grid(37.5665, 126.9780), (60, 127))

    def test_resolver_accepts_either_grid_or_latlon_pair(self):
        self.assertEqual(resolve_grid_coordinates(nx=60, ny=127), (60, 127))
        self.assertEqual(
            resolve_grid_coordinates(latitude=37.5665, longitude=126.9780),
            (60, 127),
        )

    def test_incomplete_or_mixed_coordinates_are_rejected(self):
        invalid_values = (
            {"nx": 60},
            {"latitude": 37.5665},
            {"nx": 60, "ny": 127, "latitude": 37.5665, "longitude": 126.9780},
            {},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(KmaGridError):
                resolve_grid_coordinates(**values)

    def test_location_outside_kma_grid_is_rejected(self):
        with self.assertRaises(KmaGridError):
            latlon_to_grid(0, 0)

    def test_projection_poles_are_rejected_as_invalid_latitude(self):
        for latitude in (-90, 90):
            with self.subTest(latitude=latitude), self.assertRaises(KmaGridError):
                latlon_to_grid(latitude, 126)


if __name__ == "__main__":
    unittest.main()
