import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession

from app.database import get_db
from app.models import Session, Subscription, User
from app.schemas import LoginRequest, RegisterRequest, SubscriptionResponse, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_MAX_AGE = 7 * 86400  # 7 days in seconds


def get_current_user(request: Request, db: DBSession = Depends(get_db)) -> User:
    session_id = request.cookies.get("session_id")
    all_cookies = dict(request.cookies)
    logger.info("get_current_user: cookies=%s, session_id=%s", list(all_cookies.keys()), session_id[:8] if session_id else None)

    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = (
        db.query(Session)
        .filter(Session.id == session_id, Session.expires_at > datetime.now(timezone.utc))
        .first()
    )
    if not session:
        logger.warning("Session not found or expired for id=%s...", session_id[:8])
        raise HTTPException(status_code=401, detail="Session expired")

    user = db.query(User).filter(User.id == session.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    logger.info("Authenticated user: %s (%s)", user.email, user.id)
    return user


def _create_session(db: DBSession, user_id, response: Response) -> None:
    session_id = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(days=7)

    db.add(Session(id=session_id, user_id=user_id, expires_at=expires))
    db.commit()

    response.set_cookie(
        "session_id",
        session_id,
        httponly=True,
        samesite="none",
        secure=True,
        max_age=SESSION_MAX_AGE,
    )
    logger.info("Created session %s... for user %s", session_id[:8], user_id)


@router.post("/register")
def register(body: RegisterRequest, response: Response, db: DBSession = Depends(get_db)) -> UserResponse:
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    password_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    user = User(email=body.email, password_hash=password_hash, display_name=body.display_name)
    db.add(user)
    db.commit()
    db.refresh(user)

    _create_session(db, user.id, response)

    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
    )


@router.post("/login")
def login(body: LoginRequest, response: Response, db: DBSession = Depends(get_db)) -> UserResponse:
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    _create_session(db, user.id, response)

    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
    )


@router.post("/logout")
def logout(request: Request, response: Response, db: DBSession = Depends(get_db)):
    session_id = request.cookies.get("session_id")
    if session_id:
        db.query(Session).filter(Session.id == session_id).delete()
        db.commit()

    response.delete_cookie("session_id")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
    )


@router.get("/subscription")
def get_subscription(
    user: User = Depends(get_current_user), db: DBSession = Depends(get_db)
) -> SubscriptionResponse | None:
    sub = (
        db.query(Subscription)
        .filter(Subscription.user_id == user.id)
        .order_by(Subscription.created_at.desc())
        .first()
    )
    if not sub:
        return None
    return SubscriptionResponse(
        id=sub.id,
        status=sub.status,
        price_id=sub.price_id,
        current_period_end=sub.current_period_end,
    )
