#!/usr/bin/env python3
"""
Reset database - drops all tables and recreates them
USE WITH CAUTION!
"""
import logging
from database import Base
from db_session import get_engine, init_database

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

def reset_database():
    """Drop all tables and recreate"""
    logger.warning("============================================================")
    logger.warning("WARNING: This will DELETE ALL DATA in the database!")
    logger.warning("============================================================")
    
    engine = get_engine()
    
    # Drop all tables
    logger.info("Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    logger.info("All tables dropped")
    
    # Recreate tables
    logger.info("Creating fresh database schema...")
    init_database()
    logger.info("Database schema created successfully")
    
    logger.info("============================================================")
    logger.info("Database reset complete. Ready for migration.")
    logger.info("============================================================")

if __name__ == "__main__":
    reset_database()
