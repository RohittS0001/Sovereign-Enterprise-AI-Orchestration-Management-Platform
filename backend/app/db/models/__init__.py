"""Import every model here so ``Base.metadata`` is complete for Alembic and tests."""

from backend.app.db.models.refresh_token import RefreshToken
from backend.app.db.models.user import User

__all__ = ["RefreshToken", "User"]
