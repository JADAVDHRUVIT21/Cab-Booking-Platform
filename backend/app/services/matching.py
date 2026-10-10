from math import asin, cos, radians, sin, sqrt

from sqlalchemy.orm import Session

from app.models.user import User, UserRole


MAX_DRIVER_DISTANCE_KM = 10.0


def calculate_distance_km(
    latitude_1: float,
    longitude_1: float,
    latitude_2: float,
    longitude_2: float,
) -> float:
    earth_radius_km = 6371.0

    lat1 = radians(latitude_1)
    lat2 = radians(latitude_2)

    delta_lat = radians(latitude_2 - latitude_1)
    delta_lng = radians(longitude_2 - longitude_1)

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1)
        * cos(lat2)
        * sin(delta_lng / 2) ** 2
    )

    c = 2 * asin(sqrt(a))

    return earth_radius_km * c


def find_nearest_driver(
    db: Session,
    pickup_lat: float,
    pickup_lng: float,
) -> User | None:
    drivers = (
        db.query(User)
        .filter(
            User.role == UserRole.DRIVER,
            User.is_available.is_(True),
            User.current_lat.is_not(None),
            User.current_lng.is_not(None),
        )
        .all()
    )

    nearest_driver = None
    nearest_distance = MAX_DRIVER_DISTANCE_KM

    for driver in drivers:
        distance = calculate_distance_km(
            latitude_1=pickup_lat,
            longitude_1=pickup_lng,
            latitude_2=driver.current_lat,
            longitude_2=driver.current_lng,
        )

        if distance <= nearest_distance:
            nearest_distance = distance
            nearest_driver = driver

    return nearest_driver

if __name__ == "__main__":
    from app.core.database import SessionLocal

    db = SessionLocal()

    try:
        driver = find_nearest_driver(
            db=db,
            pickup_lat=22.3039,
            pickup_lng=70.8022,
        )

        if driver:
            print(
                f"Nearest driver: {driver.id} - {driver.name}"
            )
            print(
                f"Driver location: "
                f"{driver.current_lat}, "
                f"{driver.current_lng}"
            )
        else:
            print("No available driver found")
    finally:
        db.close()