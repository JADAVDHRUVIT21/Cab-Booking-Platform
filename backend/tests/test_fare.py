import pytest

from app.services.fare import calculate_distance_km, calculate_fare


def test_same_pickup_and_dropoff_distance_is_zero():
    distance = calculate_distance_km(
        22.3039, 70.8022,
        22.3039, 70.8022,
    )

    assert distance == pytest.approx(0.0, abs=0.001)


def test_distance_between_rajkot_locations_is_positive():
    distance = calculate_distance_km(
        22.3039, 70.8022,
        22.3094, 70.7797,
    )

    assert distance > 0
    assert distance < 10


def test_fare_has_base_fare_for_zero_distance():
    fare = calculate_fare(0)

    assert fare == pytest.approx(50.0)


def test_fare_increases_with_distance():
    short_trip_fare = calculate_fare(2)
    long_trip_fare = calculate_fare(5)

    assert long_trip_fare > short_trip_fare


def test_fare_matches_current_pricing_formula():
    fare = calculate_fare(2)

    assert fare == pytest.approx(80.0)