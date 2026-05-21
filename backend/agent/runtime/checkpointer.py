from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from config.settings import Settings


class MySQLCheckpointerManager:
    """Manage the lifetime of the async LangGraph MySQL checkpointer."""

    def __init__(self, app_settings: Settings) -> None:
        self._settings = app_settings
        self._exit_stack = AsyncExitStack()
        self._checkpointer: Any | None = None

    async def setup(self) -> Any:
        """Open the MySQL checkpoint connection and create tables if needed."""
        if self._checkpointer is not None:
            return self._checkpointer

        try:
            from asyncmy import connect
            from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver
        except ImportError as exc:
            raise RuntimeError(
                "Missing MySQL checkpoint dependency. Install "
                "`langgraph-checkpoint-mysql[asyncmy]` in the Python 3.12 backend environment."
            ) from exc

        # Keep charset explicit to match the regular MySQL connection settings.
        connection = await self._exit_stack.enter_async_context(
            connect(
                host=self._settings.mysql_host,
                user=self._settings.mysql_user,
                password=self._settings.mysql_password,
                db=self._settings.mysql_database,
                port=self._settings.mysql_port,
                charset="utf8mb4",
                autocommit=True,
            )
        )
        self._checkpointer = AsyncMySaver(conn=connection)
        await self._checkpointer.setup()
        return self._checkpointer

    @property
    def checkpointer(self) -> Any:
        """Return the active checkpointer after application startup."""
        if self._checkpointer is None:
            raise RuntimeError("MySQL checkpointer has not been initialized.")
        return self._checkpointer

    async def close(self) -> None:
        """Close the checkpoint connection held by the async context manager."""
        await self._exit_stack.aclose()
        self._exit_stack = AsyncExitStack()
        self._checkpointer = None
