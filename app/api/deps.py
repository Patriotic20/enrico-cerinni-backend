from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.utils.auth import get_current_user_payload, get_token_from_cookie
from app.models.user import User, UserRole
from app.services.auth_service import AuthService

security = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Get current authenticated user from either header or cookie."""
    token = None

    # Try header-based auth first (standard for APIs and production)
    if credentials:
        token = credentials.credentials
    
    # Fallback to cookie-based auth (useful for development and browser requests)
    if not token:
        token = get_token_from_cookie(request, "access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No authentication token found. Please provide a valid Bearer token in Authorization header or access_token cookie.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = get_current_user_payload(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(payload.get("sub"))
    auth_service = AuthService(db)
    user = auth_service.get_user_by_id(user_id)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user"
        )
    return current_user


def require_roles(*roles: UserRole):
    """Dependency factory: reject callers whose role is not in `roles`."""

    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
            )
        return current_user

    return checker


# Admins and managers; cashiers (UserRole.USER) only sell, manage clients and
# collect debts.
require_staff = require_roles(UserRole.ADMIN, UserRole.MANAGER)


def is_staff(user: User) -> bool:
    """Cashiers must not see purchase prices (margins)."""
    return user.role in (UserRole.ADMIN, UserRole.MANAGER)


def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current admin user."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions"
        )
    return current_user
