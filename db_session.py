"""
Database session management and initialization.
"""
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
import logging

from database import Base
from config import get_config

logger = logging.getLogger(__name__)

# Global engine and session factory
_engine = None
_SessionLocal = None


def init_database():
    """Initialize database engine and create tables"""
    global _engine, _SessionLocal
    
    config = get_config()
    
    safe_database_url = make_url(config.database_url).render_as_string(hide_password=True)
    logger.info("Initializing database: %s", safe_database_url)
    
    # Create engine
    _engine = create_engine(
        config.database_url,
        echo=config.database_echo,
        poolclass=NullPool if config.database_url.startswith("sqlite") else None,
        pool_pre_ping=True,  # Verify connections before using
    )
    
    # Create session factory
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    
    # Create all tables
    Base.metadata.create_all(bind=_engine)
    
    logger.info("Database initialized successfully")


def get_engine():
    """Get the database engine"""
    global _engine
    if _engine is None:
        init_database()
    return _engine


def get_session_factory():
    """Get the session factory"""
    global _SessionLocal
    if _SessionLocal is None:
        init_database()
    return _SessionLocal


@contextmanager
def get_db_session() -> Session:
    """
    Context manager for database sessions.
    
    Usage:
        with get_db_session() as session:
            # Use session here
            pass
    """
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_session() -> Session:
    """
    Create a new database session.
    Caller is responsible for closing the session.
    """
    SessionLocal = get_session_factory()
    return SessionLocal()


def close_database():
    """Close database connections"""
    global _engine
    if _engine:
        _engine.dispose()
        logger.info("Database connections closed")
