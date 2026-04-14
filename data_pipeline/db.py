"""
AirScout Centralized Database Module
=====================================

Single source of truth for database connections across all pipeline scripts.
Eliminates duplicated get_database_url() / get_engine() functions.
"""

import os
import logging
from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import create_engine, Engine

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """Build Supabase PostgreSQL connection URL from environment."""
    host = os.environ.get("SUPABASE_DB_HOST")
    port = os.environ.get("SUPABASE_DB_PORT", "5432")
    dbname = os.environ.get("SUPABASE_DB_NAME", "postgres")
    user = os.environ.get("SUPABASE_DB_USER", "postgres")
    password = os.environ.get("SUPABASE_DB_PASSWORD")

    if not host or not password:
        raise ValueError(
            "Missing required environment variables: "
            "SUPABASE_DB_HOST and SUPABASE_DB_PASSWORD must be set"
        )

    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create a cached SQLAlchemy engine (one per process)."""
    return create_engine(get_database_url(), echo=False, pool_pre_ping=True)
