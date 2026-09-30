from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional
from app.models.user import User, UserRole
from app.schemas.auth import UserLogin, UserRegister, UserResponse
from app.utils.auth import (
    MAX_PASSWORD_BYTES,
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
)
from app.utils.helpers import validate_email, normalize_phone
from app.models.employee import Employee
from fastapi import HTTPException, status


# Compared against when the email is unknown, so a miss costs the same bcrypt
# time as a hit and response timing can't reveal which emails exist.
_DUMMY_HASH = get_password_hash("timing-equaliser")


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def authenticate_user(self, email: str, password: str) -> Optional[User]:
        user = self.db.query(User).filter(User.email == email).first()
        if not user:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user

    def authenticate_seller(self, phone: str, pin: str) -> Optional[User]:
        """Mobile app login: employee phone + PIN, same timing on a miss."""
        user = (
            self.db.query(User)
            .filter(User.role == UserRole.SELLER, User.phone == normalize_phone(phone))
            .first()
        )
        if not user:
            verify_password(pin, _DUMMY_HASH)
            return None
        if not verify_password(pin, user.hashed_password):
            return None
        return user

    def sync_seller_account(self, employee: Employee, pin: Optional[str] = None) -> None:
        """Keep the employee's mobile login (a SELLER user) in step with them.

        A PIN creates the account or replaces the PIN (signing out the phone);
        without one, an existing account just follows the phone/name. Caller commits.
        """
        user = self.db.query(User).filter(User.employee_id == employee.id).first()
        if user is None and not pin:
            return
        phone = normalize_phone(employee.phone)
        if len(phone) < 9:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Employee needs a phone number for mobile access",
            )
        taken = (
            self.db.query(User.id)
            .filter(User.role == UserRole.SELLER, User.phone == phone, User.employee_id != employee.id)
            .first()
        )
        if taken:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Another seller already uses this phone number",
            )
        if user is None:
            user = User(
                username=f"seller-{employee.id}",
                role=UserRole.SELLER,
                employee_id=employee.id,
                token_version=0,
            )
            self.db.add(user)
        user.phone = phone
        user.first_name = employee.first_name
        user.last_name = employee.last_name
        if pin:
            user.hashed_password = get_password_hash(pin)
            user.token_version = (user.token_version or 0) + 1

    def create_user(self, user_data: UserRegister) -> User:
        if not validate_email(user_data.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email format"
            )

        existing_user = (
            self.db.query(User)
            .filter(
                (User.email == user_data.email) | (User.username == user_data.username)
            )
            .first()
        )

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email or username already exists",
            )

        self._check_password_length(user_data.password)
        hashed_password = get_password_hash(user_data.password)
        db_user = User(
            email=user_data.email,
            username=user_data.username,
            first_name=user_data.first_name,
            last_name=user_data.last_name,
            phone=user_data.phone,
            hashed_password=hashed_password,
            # Role is assigned here, never taken from the request payload.
            role=UserRole.MANAGER,
        )

        try:
            self.db.add(db_user)
            self.db.commit()
            self.db.refresh(db_user)
            return db_user
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="User creation failed"
            )

    def login_user(self, user_data: UserLogin) -> dict:
        user = self.authenticate_user(user_data.email, user_data.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is disabled",
            )

        return self.issue_tokens(user)

    @staticmethod
    def issue_tokens(user: User) -> dict:
        token_data = {"sub": str(user.id), "email": user.email, "role": user.role.value}
        access_token = create_access_token(data=token_data)
        refresh_token = create_refresh_token(data={**token_data, "ver": user.token_version})

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": 30 * 60,  # 30 minutes
        }

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id).first()

    def get_user_by_email(self, email: str) -> Optional[User]:
        return self.db.query(User).filter(User.email == email).first()

    def get_user_by_username(self, username: str) -> Optional[User]:
        return self.db.query(User).filter(User.username == username).first()

    def update_user(self, user_id: int, **kwargs) -> Optional[User]:
        user = self.get_user_by_id(user_id)
        if not user:
            return None

        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)

        self.db.commit()
        self.db.refresh(user)
        return user

    def logout_user(self, user_id: int):
        user = self.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )
        # Invalidate every refresh token issued so far.
        user.token_version += 1
        self.db.commit()
        return user

    @staticmethod
    def _check_password_length(password: str) -> None:
        if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Password must be at most {MAX_PASSWORD_BYTES} bytes",
            )

    def change_password(self, user: User, current_password: str, new_password: str) -> dict:
        if not verify_password(current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )
        self._check_password_length(new_password)
        user.hashed_password = get_password_hash(new_password)
        user.token_version += 1
        self.db.commit()
        return self.issue_tokens(user)
