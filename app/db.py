"""Database connection.

The engine holds a small pool of open connections that requests reuse, instead of
opening a new connection (slow) for every request.
"""

from sqlalchemy import create_engine

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
