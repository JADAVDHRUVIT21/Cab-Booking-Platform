
from fastapi import FastAPI

from app.core.config import settings
from app.routers.auth import router as auth_router
from app.routers.rides import router as rides_router
from app.routers.websockets import router as websockets_router
from app.routers.reviews import router as reviews_router
from app.routers.promo_codes import router as promo_codes_router
from app.routers.payments import router as payments_router
from app.routers.payment_webhooks import router as payment_webhooks_router

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
)

app.include_router(auth_router)
app.include_router(rides_router)
app.include_router(websockets_router)
app.include_router(reviews_router)
app.include_router(promo_codes_router)
app.include_router(payments_router)
app.include_router(payment_webhooks_router)

@app.get("/")
def root():
    return {
        "message": "Cab Booking Platform API is running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }