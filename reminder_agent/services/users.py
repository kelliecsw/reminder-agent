from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reminder_agent.db.database import Database
from reminder_agent.domain.errors import InvalidTimezoneError
from reminder_agent.repositories.users import UserRepository

from ._common import require_user


class UserService:
    def __init__(
        self,
        database: Database,
        user_repo: UserRepository | None = None,
    ) -> None:
        self.database = database
        self.user_repo = user_repo or UserRepository()

    def get_timezone(self, user_id: int) -> str:
        with self.database.connect() as conn:
            return require_user(conn, self.user_repo, user_id)

    def update_timezone(self, user_id: int, timezone: str) -> str:
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidTimezoneError(
                f"Unknown IANA timezone: {timezone}"
            ) from exc

        with self.database.transaction() as conn:
            require_user(conn, self.user_repo, user_id)
            self.user_repo.update_timezone(conn, user_id, timezone=timezone)
        return timezone

