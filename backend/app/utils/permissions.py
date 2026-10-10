
from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.models.user import User
from app.utils.auth import get_current_user


def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    allowed_emails = {
        email.strip().lower()
        for email in settings.ADMIN_EMAILS.split(",")
        if email.strip()
    }

    if not allowed_emails or current_user.email.lower() not in allowed_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )

    return current_user
