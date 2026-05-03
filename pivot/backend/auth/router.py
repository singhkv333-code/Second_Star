import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import User
from backend.schemas import UserCreate, UserLogin, TokenResponse, UserResponse
from backend.auth.jwt_handler import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    get_user_id_from_token,
)
from backend.services.demo_seeder import seed_demo_data

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """Register a new user and return JWT tokens."""

    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Seed demo data so a fresh account doesn't land on empty Agents /
    # Portfolio / Order-history tabs. Failures here are logged but
    # never block registration — the user can still use the app, just
    # without preloaded examples.
    try:
        seed_result = seed_demo_data(db, user.id)
        if not seed_result.get("skipped"):
            logger.info(
                "Seeded demo data for user %s: %d workflows, %d trades",
                user.id, seed_result.get("workflows", 0),
                seed_result.get("trades", 0),
            )
    except Exception as e:
        logger.warning("Demo seed raised for user %s: %s", user.id, e)

    access_token = create_access_token(user.id, user.email)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        email=user.email,
    )


@router.get("/me", response_model=UserResponse)
def me(
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Return the authenticated user's profile.

    Used by the frontend dashboard ("Good Evening {name}!" greeting)
    and anywhere we need the current user's display name without
    re-decoding the JWT in the browser.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )
    token = authorization.replace("Bearer ", "", 1)
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(credentials: UserLogin, db: Session = Depends(get_db)):
    """Login with email/password and return JWT tokens."""

    user = db.query(User).filter(User.email == credentials.email).first()

    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    access_token = create_access_token(user.id, user.email)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        email=user.email,
    )
