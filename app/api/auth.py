from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.auth_service import AuthService
from app.schemas.auth import UserLogin, UserRegister, UserResponse, PasswordChange
from app.schemas.common import ResponseModel
from app.utils.auth import (
    get_user_from_refresh_token,
    set_auth_cookies,
    clear_auth_cookies,
    get_token_from_cookie,
)
from app.api.deps import get_current_admin_user, get_current_user
from app.models.user import User
from app.utils.rate_limit import SlidingWindow

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Brute-force guard: attempts per client IP, and failures per account.
_login_ip = SlidingWindow(limit=20, seconds=60)
_login_fail = SlidingWindow(limit=5, seconds=15 * 60)
_TOO_MANY = "Too many login attempts. Try again later."


# Plain `def`: bcrypt and the DB calls block, so FastAPI must run this in its threadpool.
@router.post("/login", response_model=ResponseModel)
def login(
    user_data: UserLogin, request: Request, response: Response, db: Session = Depends(get_db)
):
    ip = request.client.host if request.client else "unknown"
    email_key = user_data.email.strip().lower()
    if _login_ip.blocked(ip) or _login_fail.blocked(email_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_TOO_MANY)
    _login_ip.hit(ip)

    auth_service = AuthService(db)
    try:
        try:
            tokens = auth_service.login_user(user_data)
        except HTTPException:
            _login_fail.hit(email_key)
            raise
        _login_fail.reset(email_key)

        # Set authentication cookies (with environment-appropriate security settings)
        set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])

        # Get user data for response
        user = auth_service.get_user_by_email(user_data.email)

        return ResponseModel(
            success=True,
            data={
                "id": user.id,
                "email": user.email,
                "name": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "phone": user.phone,
                "role": user.role.value,
                "created_at": user.created_at.isoformat(),
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
            },
            message="Login successful",
        )
    except HTTPException as e:
        return ResponseModel(success=False, message=e.detail)


@router.post("/register", response_model=ResponseModel)
def register(
    user_data: UserRegister,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin_user),
):
    """Create a staff account. Admin-only: this endpoint used to be public and to
    accept the new user's role, which allowed anyone to create an admin for
    themselves. It no longer signs the caller in as the created user."""
    auth_service = AuthService(db)
    try:
        user = auth_service.create_user(user_data)
        return ResponseModel(
            success=True,
            data={
                "id": user.id,
                "email": user.email,
                "name": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "phone": user.phone,
                "role": user.role.value,
                "created_at": user.created_at.isoformat(),
            },
            message="User created successfully",
        )
    except HTTPException as e:
        return ResponseModel(success=False, message=e.detail)


@router.post("/refresh", response_model=ResponseModel)
async def refresh_token(
    request: Request, response: Response, db: Session = Depends(get_db)
):
    # Get refresh token from cookie
    refresh_token = get_token_from_cookie(request, "refresh_token")

    # If not in cookie, try from JSON body
    if not refresh_token:
        try:
            body = await request.json()
            refresh_token = body.get("refresh_token")
        except Exception:
            pass

    # If still not found, try from Authorization header
    if not refresh_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            refresh_token = auth_header.split(" ")[1]

    if not refresh_token:
        return ResponseModel(success=False, message="No refresh token found")

    payload = get_user_from_refresh_token(refresh_token)
    if not payload:
        return ResponseModel(success=False, message="Invalid refresh token")

    # Re-check the account: deleted/disabled users and tokens revoked by
    # logout or password change must not mint new tokens.
    user = db.get(User, int(payload.get("sub")))
    if not user or not user.is_active or payload.get("ver") != user.token_version:
        return ResponseModel(success=False, message="Invalid refresh token")

    tokens = AuthService.issue_tokens(user)
    access_token = tokens["access_token"]
    new_refresh_token = tokens["refresh_token"]

    # Set new cookies (for cookie-based clients)
    set_auth_cookies(response, access_token, new_refresh_token)

    return ResponseModel(
        success=True,
        data={
            "access_token": access_token,
            "refresh_token": new_refresh_token,
        },
        message="Token refreshed successfully",
    )


@router.get("/validate", response_model=ResponseModel)
def validate_token(current_user=Depends(get_current_user)):
    return ResponseModel(
        success=True,
        data={
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.username,
            "first_name": current_user.first_name,
            "last_name": current_user.last_name,
            "phone": current_user.phone,
            "role": current_user.role.value,
            "created_at": current_user.created_at.isoformat(),
        },
        message="Token is valid",
    )


@router.post("/change-password", response_model=ResponseModel)
def change_password(
    data: PasswordChange,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Change own password. Revokes all other sessions and returns fresh tokens."""
    tokens = AuthService(db).change_password(
        current_user, data.current_password, data.new_password
    )
    set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])
    return ResponseModel(
        success=True,
        data={"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]},
        message="Password changed",
    )


@router.post("/logout", response_model=ResponseModel)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    auth_service = AuthService(db)
    try:
        auth_service.logout_user(current_user.id)
        # Clear cookies
        clear_auth_cookies(response)
    except HTTPException as e:
        return ResponseModel(success=False, message=e.detail)
    return ResponseModel(success=True, message="Logout successful")
