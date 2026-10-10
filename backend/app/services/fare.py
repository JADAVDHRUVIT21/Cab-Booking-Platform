from math import asin, cos, radians, sin, sqrt


BASE_FARE = 50.0
PER_KM_RATE = 15.0


def calculate_distance_km(
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
) -> float:
    """
    Calculate approximate distance between two coordinates
    using the Haversine formula.
    """

    earth_radius_km = 6371.0

    lat1 = radians(pickup_lat)
    lat2 = radians(dropoff_lat)

    delta_lat = radians(dropoff_lat - pickup_lat)
    delta_lng = radians(dropoff_lng - pickup_lng)

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1)
        * cos(lat2)
        * sin(delta_lng / 2) ** 2
    )

    c = 2 * asin(sqrt(a))

    return round(earth_radius_km * c, 2)


def calculate_fare(distance_km: float) -> float:
    """
    Calculate estimated ride fare.
    """

    fare = BASE_FARE + (distance_km * PER_KM_RATE)

    return round(fare, 2)