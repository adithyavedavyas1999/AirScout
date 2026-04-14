"""Tests for route parsing and buffer creation."""

import pytest
from shapely.geometry import LineString

from data_pipeline.check_route import (
    parse_route_coordinates,
    parse_route_wkt,
    create_route_buffer,
    calculate_route_length_km,
)


class TestParseRouteCoordinates:
    def test_valid_route(self):
        coords = [[-87.63, 41.88], [-87.64, 41.89]]
        line = parse_route_coordinates(coords)
        assert isinstance(line, LineString)
        assert len(line.coords) == 2

    def test_too_few_points(self):
        with pytest.raises(ValueError, match="at least 2"):
            parse_route_coordinates([[-87.63, 41.88]])

    def test_three_point_route(self):
        coords = [[-87.63, 41.88], [-87.64, 41.89], [-87.65, 41.90]]
        line = parse_route_coordinates(coords)
        assert len(line.coords) == 3


class TestParseRouteWkt:
    def test_valid_wkt(self):
        wkt = "LINESTRING(-87.63 41.88, -87.64 41.89)"
        line = parse_route_wkt(wkt)
        assert isinstance(line, LineString)


class TestCreateRouteBuffer:
    def test_buffer_creates_polygon(self):
        line = LineString([(-87.63, 41.88), (-87.64, 41.89)])
        buffered = create_route_buffer(line, 25)
        geom = buffered.geometry.iloc[0]
        assert geom.geom_type == "Polygon"
        assert geom.area > 0


class TestRouteLength:
    def test_nonzero_length(self):
        line = LineString([(-87.63, 41.88), (-87.64, 41.89)])
        km = calculate_route_length_km(line)
        assert km > 0

    def test_reasonable_chicago_distance(self):
        line = LineString([(-87.6298, 41.8781), (-87.6450, 41.9150)])
        km = calculate_route_length_km(line)
        assert 3 < km < 6
