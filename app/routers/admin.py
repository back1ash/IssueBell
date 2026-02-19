"""Admin endpoints — accessible only to the configured admin Discord user."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models import Subscription, User

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, user_id)
    if user is None or user.discord_id != settings.admin_discord_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


@router.get("/users")
def list_users(
    q: str = Query(default="", description="Filter by Discord or GitHub username"),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return all users with their subscriptions. Optionally filter by username."""
    query = db.query(User).options(joinedload(User.subscriptions))
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            User.username.ilike(like) | User.github_username.ilike(like)
        )
    users = query.order_by(User.username).all()
    return [
        {
            "id": u.id,
            "discord_id": u.discord_id,
            "username": u.username,
            "avatar": u.avatar,
            "github_username": u.github_username,
            "github_connected": u.github_token is not None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "subscriptions": [
                {
                    "id": s.id,
                    "repo_full_name": s.repo_full_name,
                    "label": s.label,
                    "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
                }
                for s in u.subscriptions
            ],
        }
        for u in users
    ]
