from unittest.mock import MagicMock

from app.services.matching import find_nearest_driver


def test_returns_nearest_available_driver():
    nearest_driver = MagicMock()
    nearest_driver.id = 1
    nearest_driver.current_lat = 22.3039
    nearest_driver.current_lng = 70.8022

    farther_driver = MagicMock()
    farther_driver.id = 2
    farther_driver.current_lat = 22.3100
    farther_driver.current_lng = 70.8100

    query = MagicMock()
    query.filter.return_value.all.return_value = [
        farther_driver,
        nearest_driver,
    ]

    db = MagicMock()
    db.query.return_value = query

    result = find_nearest_driver(
        db,
        pickup_lat=22.3039,
        pickup_lng=70.8022,
    )

    assert result is nearest_driver


def test_returns_none_when_no_drivers_are_available():
    query = MagicMock()
    query.filter.return_value.all.return_value = []

    db = MagicMock()
    db.query.return_value = query

    result = find_nearest_driver(
        db,
        pickup_lat=22.3039,
        pickup_lng=70.8022,
    )

    assert result is None