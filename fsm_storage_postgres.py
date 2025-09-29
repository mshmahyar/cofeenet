# fsm_storage_postgres.py
import json
import asyncpg
from typing import Optional, Dict, Any


class PostgresStorage:
    """
    Lightweight FSM storage for aiogram v2 using asyncpg + a single table.
    Methods mirror what aiogram v2 expects: set_state, get_state, set_data,
    get_data, update_data, reset_data, reset_state (finish), close.
    """

    def __init__(self, pool: asyncpg.pool.Pool):
        self.pool = pool

    async def create_table(self) -> None:
        """Create table if not exists."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fsm_storage (
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    state TEXT,
                    data JSONB,
                    updated_at TIMESTAMP DEFAULT now(),
                    PRIMARY KEY (chat_id, user_id)
                );
                """
            )

    def _ids(self, chat, user):
        """Normalize chat/user arguments to integers (chat_id, user_id)."""
        if chat is None or user is None:
            raise ValueError("chat and user must be provided")
        chat_id = chat if isinstance(chat, int) else getattr(chat, "id", None)
        user_id = user if isinstance(user, int) else getattr(user, "id", None)
        if chat_id is None or user_id is None:
            raise ValueError("unable to determine chat_id/user_id")
        return int(chat_id), int(user_id)

    # ----- State methods -----
    async def set_state(self, chat=None, user=None, state: Optional[str] = None):
        chat_id, user_id = self._ids(chat, user)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fsm_storage(chat_id, user_id, state, data, updated_at)
                VALUES($1, $2, $3, '{}'::jsonb, NOW())
                ON CONFLICT (chat_id,user_id)
                DO UPDATE SET state = $3, updated_at = NOW();
                """,
                chat_id,
                user_id,
                state,
            )

    async def get_state(self, chat=None, user=None) -> Optional[str]:
        chat_id, user_id = self._ids(chat, user)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state FROM fsm_storage WHERE chat_id=$1 AND user_id=$2",
                chat_id,
                user_id,
            )
            return row["state"] if row else None

    async def reset_state(self, chat=None, user=None):
        """Clear state and data (like finishing the FSM)."""
        chat_id, user_id = self._ids(chat, user)
        async with self.pool.acquire() as conn:
            # here we clear state and data but keep row (or create row with empty data)
            await conn.execute(
                """
                INSERT INTO fsm_storage(chat_id, user_id, state, data, updated_at)
                VALUES($1, $2, NULL, '{}'::jsonb, NOW())
                ON CONFLICT (chat_id,user_id) DO UPDATE SET state=NULL, data='{}'::jsonb, updated_at=NOW();
                """,
                chat_id,
                user_id,
            )

    # alias for compatibility
    finish = reset_state

    # ----- Data methods -----
    async def set_data(self, chat=None, user=None, data: Optional[Dict[str, Any]] = None):
        chat_id, user_id = self._ids(chat, user)
        safe = data or {}
        payload = json.dumps(safe, ensure_ascii=False)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fsm_storage(chat_id, user_id, state, data, updated_at)
                VALUES($1, $2, NULL, $3::jsonb, NOW())
                ON CONFLICT (chat_id,user_id) DO UPDATE SET data=$3::jsonb, updated_at=NOW();
                """,
                chat_id,
                user_id,
                payload,
            )

    async def get_data(self, chat=None, user=None) -> Dict[str, Any]:
        chat_id, user_id = self._ids(chat, user)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM fsm_storage WHERE chat_id=$1 AND user_id=$2",
                chat_id,
                user_id,
            )
            if not row or row["data"] is None:
                return {}
            return row["data"]

    async def update_data(self, chat=None, user=None, data: Optional[Dict[str, Any]] = None):
        """Merge provided dict into existing data."""
        if not data:
            return
        chat_id, user_id = self._ids(chat, user)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM fsm_storage WHERE chat_id=$1 AND user_id=$2",
                chat_id,
                user_id,
            )
            existing = row["data"] if row and row["data"] else {}
            # shallow merge
            merged = {**existing, **data}
            await conn.execute(
                """
                INSERT INTO fsm_storage(chat_id, user_id, state, data, updated_at)
                VALUES($1, $2, NULL, $3::jsonb, NOW())
                ON CONFLICT (chat_id,user_id) DO UPDATE SET data=$3::jsonb, updated_at=NOW();
                """,
                chat_id,
                user_id,
                json.dumps(merged, ensure_ascii=False),
            )

    async def reset_data(self, chat=None, user=None):
        chat_id, user_id = self._ids(chat, user)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fsm_storage(chat_id, user_id, state, data, updated_at)
                VALUES($1, $2, NULL, '{}'::jsonb, NOW())
                ON CONFLICT (chat_id,user_id) DO UPDATE SET data='{}'::jsonb, updated_at=NOW();
                """,
                chat_id,
                user_id,
            )

    # ----- housekeeping -----
    async def close(self):
        await self.pool.close()
