
import logging
import os
import smtplib
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from pathlib import Path
from xml.sax.saxutils import escape

from celery.utils.log import get_task_logger
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.ride import Ride, RideStatus
from app.models.user import User


logger = get_task_logger(__name__)


@celery_app.task(name="app.services.background_tasks.health_check")
def health_check() -> dict:
    """Verify that Celery background execution works."""
    return {
        "status": "success",
        "message": "Cab Booking Platform background worker is running.",
    }


def _safe_text(value) -> str:
    """Escape text before inserting it into a ReportLab Paragraph."""
    if value is None or str(value).strip() == "":
        return "Not provided"
    return escape(str(value))


def _money(value) -> Decimal:
    """Convert a fare to a two-decimal monetary value."""
    return Decimal(str(value)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _create_receipt_pdf(receipt: dict) -> str:
    """Create a PDF receipt and return its absolute file path."""
    receipt_dir = Path(settings.RECEIPT_DIR)

    if not receipt_dir.is_absolute():
        receipt_dir = Path.cwd() / receipt_dir

    receipt_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = (
        receipt_dir
        / f"receipt_{receipt['receipt_number']}.pdf"
    ).resolve()

    document = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Ride Receipt {receipt['receipt_number']}",
        author=settings.APP_NAME,
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReceiptTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#173B72"),
            spaceAfter=8,
        )
    )

    story = [
        Paragraph(
            escape(settings.APP_NAME),
            styles["ReceiptTitle"],
        ),
        Paragraph("Ride Payment Receipt", styles["Heading2"]),
        Spacer(1, 8),
        Paragraph(
            f"<b>Receipt number:</b> "
            f"{_safe_text(receipt['receipt_number'])}",
            styles["Normal"],
        ),
        Paragraph(
            f"<b>Ride ID:</b> {_safe_text(receipt['ride_id'])}",
            styles["Normal"],
        ),
        Paragraph(
            f"<b>Generated:</b> "
            f"{_safe_text(receipt['generated_at'])}",
            styles["Normal"],
        ),
        Spacer(1, 12),
        Paragraph("Passenger and Driver", styles["Heading2"]),
    ]

    rider = receipt.get("rider") or {}
    driver = receipt.get("driver") or {}

    people_data = [
        ["Field", "Passenger", "Driver"],
        [
            "Name",
            _safe_text(rider.get("name")),
            _safe_text(driver.get("name")),
        ],
        [
            "Phone",
            _safe_text(rider.get("phone")),
            _safe_text(driver.get("phone")),
        ],
        [
            "Email",
            _safe_text(rider.get("email")),
            "Not provided",
        ],
        [
            "Vehicle",
            "Not applicable",
            _safe_text(driver.get("vehicle_model")),
        ],
        [
            "Vehicle number",
            "Not applicable",
            _safe_text(driver.get("vehicle_number")),
        ],
    ]

    people_table = Table(
        people_data,
        colWidths=[35 * mm, 60 * mm, 60 * mm],
        repeatRows=1,
    )
    people_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173B72")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D5DCE5")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ])
    )

    story.extend([
        people_table,
        Spacer(1, 14),
        Paragraph("Trip Details", styles["Heading2"]),
    ])

    trip_data = [
        ["Field", "Details"],
        ["Pickup", _safe_text(receipt.get("pickup_address"))],
        ["Drop-off", _safe_text(receipt.get("dropoff_address"))],
        [
            "Distance",
            f"{receipt.get('distance_km', 0):.2f} km",
        ],
        ["Ride status", _safe_text(receipt.get("ride_status"))],
        ["Requested at", _safe_text(receipt.get("requested_at"))],
        ["Completed at", _safe_text(receipt.get("completed_at"))],
    ]

    trip_table = Table(
        trip_data,
        colWidths=[45 * mm, 110 * mm],
        repeatRows=1,
    )
    trip_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173B72")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D5DCE5")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 7),
        ])
    )

    story.extend([
        trip_table,
        Spacer(1, 14),
        Paragraph("Fare Summary", styles["Heading2"]),
    ])

    fare_data = [
        ["Description", "Amount"],
        [
            "Ride fare",
            f"{receipt['currency']} {receipt['fare']}",
        ],
    ]

    fare_table = Table(
        fare_data,
        colWidths=[100 * mm, 55 * mm],
    )
    fare_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173B72")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D5DCE5")),
            ("PADDING", (0, 0), (-1, -1), 9),
        ])
    )

    story.extend([
        fare_table,
        Spacer(1, 18),
        Paragraph(
            "Thank you for riding with us.",
            styles["Normal"],
        ),
    ])

    document.build(story)
    return str(pdf_path)


def _email_receipt(receipt: dict, pdf_path: str) -> bool:
    """Email the receipt when SMTP is configured; otherwise skip it."""
    if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
        logger.info(
            "SMTP is not configured. Skipping email for ride %s.",
            receipt["ride_id"],
        )
        return False

    rider = receipt.get("rider") or {}
    recipient = rider.get("email")

    if not recipient:
        logger.info(
            "Passenger email is unavailable for ride %s.",
            receipt["ride_id"],
        )
        return False

    message = EmailMessage()
    message["Subject"] = (
        f"Your cab ride receipt - {receipt['receipt_number']}"
    )
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(
        f"Hello {rider.get('name') or 'Passenger'},\n\n"
        f"Thank you for riding with {settings.APP_NAME}.\n"
        f"Ride ID: {receipt['ride_id']}\n"
        f"Receipt: {receipt['receipt_number']}\n"
        f"Fare: {receipt['currency']} {receipt['fare']}\n\n"
        "Your PDF receipt is attached.\n"
    )

    with open(pdf_path, "rb") as pdf_file:
        message.add_attachment(
            pdf_file.read(),
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(pdf_path),
        )

    with smtplib.SMTP(
        settings.SMTP_HOST,
        settings.SMTP_PORT,
        timeout=20,
    ) as smtp:
        smtp.ehlo()

        if settings.SMTP_USE_TLS:
            smtp.starttls()
            smtp.ehlo()

        if settings.SMTP_USERNAME:
            smtp.login(
                settings.SMTP_USERNAME,
                settings.SMTP_PASSWORD or "",
            )

        smtp.send_message(message)

    return True


@celery_app.task(
    name="app.services.background_tasks.generate_ride_receipt",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def generate_ride_receipt(self, ride_id: int) -> dict:
    """Generate a PDF receipt for a completed ride and email it if configured."""
    db = SessionLocal()

    try:
        ride = db.get(Ride, ride_id)

        if ride is None:
            return {
                "status": "error",
                "message": f"Ride {ride_id} was not found.",
            }

        if ride.status != RideStatus.COMPLETED:
            return {
                "status": "error",
                "message": "A receipt can only be generated for a completed ride.",
                "ride_id": ride.id,
                "ride_status": ride.status.value,
            }

        rider = db.get(User, ride.rider_id)
        driver = (
            db.get(User, ride.driver_id)
            if ride.driver_id is not None
            else None
        )

        fare_value = (
            ride.final_fare
            if ride.final_fare is not None
            else ride.estimated_fare
        )

        fare = _money(fare_value)
        completed_at = ride.completed_at or datetime.now(timezone.utc)

        receipt = {
            "receipt_number": f"RIDE-{ride.id}",
            "ride_id": ride.id,
            "ride_status": ride.status.value,
            "rider": {
                "id": rider.id,
                "name": rider.name,
                "email": rider.email,
                "phone": rider.phone,
            } if rider else None,
            "driver": {
                "id": driver.id,
                "name": driver.name,
                "phone": driver.phone,
                "vehicle_type": driver.vehicle_type,
                "vehicle_model": driver.vehicle_model,
                "vehicle_number": driver.vehicle_number,
            } if driver else None,
            "pickup_address": ride.pickup_address,
            "dropoff_address": ride.dropoff_address,
            "distance_km": round(float(ride.estimated_distance_km), 2),
            "fare": str(fare),
            "currency": "INR",
            "fare_source": (
                "final_fare"
                if ride.final_fare is not None
                else "estimated_fare"
            ),
            "requested_at": (
                ride.requested_at.isoformat()
                if ride.requested_at
                else None
            ),
            "completed_at": completed_at.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        pdf_path = _create_receipt_pdf(receipt)

        email_sent = False
        email_error = None

        try:
            email_sent = _email_receipt(receipt, pdf_path)
        except Exception as exc:
            # Preserve the generated PDF even if email delivery fails.
            email_error = str(exc)
            logger.exception(
                "Receipt PDF generated, but email delivery failed for ride %s.",
                ride.id,
            )

        return {
            "status": "success",
            "receipt": receipt,
            "pdf_path": pdf_path,
            "email_sent": email_sent,
            "email_error": email_error,
        }

    except Exception as exc:
        logger.exception(
            "Failed to generate receipt for ride %s.",
            ride_id,
        )
        raise self.retry(exc=exc) from exc

    finally:
        db.close()
