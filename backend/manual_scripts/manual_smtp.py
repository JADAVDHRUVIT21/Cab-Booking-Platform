import smtplib
from email.message import EmailMessage
from app.core.config import settings

if not all([
    settings.SMTP_HOST,
    settings.SMTP_USERNAME,
    settings.SMTP_PASSWORD,
    settings.SMTP_FROM_EMAIL,
]):
    raise SystemExit("SMTP configuration is incomplete.")

recipient = input("Enter the email address to receive the test email: ").strip()
if not recipient or "@" not in recipient:
    raise SystemExit("Please enter a valid email address.")

message = EmailMessage()
message["Subject"] = "Cab Booking Platform - SMTP Test"
message["From"] = settings.SMTP_FROM_EMAIL
message["To"] = recipient
message.set_content(
    "SMTP test successful! Your Cab Booking Platform can send emails."
)

try:
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
        smtp.ehlo()
        if settings.SMTP_USE_TLS:
            smtp.starttls()
            smtp.ehlo()
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(message)

    print("SUCCESS: Test email sent. Check the recipient inbox and spam folder.")
except Exception as exc:
    print(f"FAILED: {type(exc).__name__}: {exc}")
    raise SystemExit(1)
