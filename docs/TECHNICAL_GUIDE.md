# Technical Guide for Developers and Maintainers

This guide covers system architecture, deployment, maintenance, and development for the Tool Signout Discord Bot.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [Technology Stack](#technology-stack)
4. [Development Setup](#development-setup)
5. [Database Schema](#database-schema)
6. [Code Structure](#code-structure)
7. [Deployment](#deployment)
8. [Database Migrations](#database-migrations)
9. [Monitoring and Logging](#monitoring-and-logging)
10. [Backup and Recovery](#backup-and-recovery)
11. [Troubleshooting](#troubleshooting)
12. [Security](#security)
13. [Performance Tuning](#performance-tuning)
14. [Contributing](#contributing)

---

## System Overview

### Purpose

Discord bot for managing tool reservations in makerspaces, workshops, and shared facilities. Features include:

- Time-based reservations with natural language parsing
- Photo enforcement for Tool Room equipment
- Role-based access control
- Consecutive signout limits
- Admin maintenance blocks
- Photo review and approval system
- Automated notifications and reminders

### Key Components

- **Discord Bot**: Python application using discord.py
- **Database**: PostgreSQL for persistent storage
- **GPT Integration**: OpenAI GPT-4 for natural language time parsing
- **Background Tasks**: Automated cleanup, notifications, photo enforcement
- **Systemd Service**: Linux service management for production deployment

---

## Architecture

### High-Level Architecture

```
┌─────────────────┐
│  Discord Users  │
└────────┬────────┘
         │
         │ Commands/Messages
         │
┌────────▼────────────────────────────┐
│       Discord Bot (mainbot.py)      │
│  ┌──────────────────────────────┐   │
│  │  Command Handlers            │   │
│  │  - /signout, /returntool     │   │
│  │  - /admin commands           │   │
│  │  - DM photo handler          │   │
│  └──────────────────────────────┘   │
│  ┌──────────────────────────────┐   │
│  │  Background Tasks            │   │
│  │  - Cleanup expired           │   │
│  │  - Send notifications        │   │
│  │  - Photo enforcement         │   │
│  └──────────────────────────────┘   │
└────────┬───────────────┬────────────┘
         │               │
         │               │ GPT API
         │               │
         │          ┌────▼──────┐
         │          │  OpenAI   │
         │          │  GPT-4    │
         │          └───────────┘
         │
         │ SQLAlchemy ORM
         │
┌────────▼────────────┐
│   PostgreSQL DB     │
│  ┌──────────────┐   │
│  │ Reservations │   │
│  │ Users        │   │
│  │ Tools        │   │
│  │ Photos       │   │
│  │ Photo Debts  │   │
│  └──────────────┘   │
└─────────────────────┘
```

### Data Flow

**Reservation Creation:**
1. User runs `/signout` command
2. GPT parses natural language time → structured datetime
3. Conflict check against database (ACTIVE + ADMIN_BLOCK reservations)
4. Photo requirement check (Tool Room detection)
5. Role permission check
6. Create reservation in database
7. Send confirmation to channel

**Photo Enforcement:**
1. Cleanup task detects expired reservation (Tool Room)
2. Check for return photo in database
3. If missing: Create photo debt, send DM request
4. User uploads photo via DM
5. Photo stored with metadata, debt cleared (if within grace period)
6. Admin reviews photos via `/admin photo view`

**Admin Blocks:**
1. Admin runs `/admin block add`
2. GPT parses time range
3. Create ADMIN_BLOCK reservation
4. Conflict checks now include ADMIN_BLOCK status
5. Users blocked from creating overlapping reservations

---

## Technology Stack

### Core Dependencies

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Runtime | Python | 3.11+ | Main application language |
| Discord Library | discord.py | 2.3+ | Discord API interaction |
| Database | PostgreSQL | 14+ | Persistent data storage |
| ORM | SQLAlchemy | 2.0+ | Database abstraction |
| HTTP Client | httpx | 0.24+ | OpenAI API calls |
| AI | OpenAI GPT-4 | Latest | Natural language parsing |
| Timezone | pytz | Latest | Timezone handling |
| Service Manager | systemd | System | Linux service management |

### Development Dependencies

- pytest: Unit testing
- black: Code formatting
- mypy: Type checking
- pylint: Code linting

### Environment

- **OS**: Linux (Ubuntu/Debian recommended)
- **Python**: Virtual environment (.bot-venv)
- **Database**: PostgreSQL on separate host or local
- **Service**: Systemd unit files

---

## Development Setup

### Prerequisites

1. Python 3.11 or higher
2. PostgreSQL 14 or higher
3. Discord bot token (from Discord Developer Portal)
4. OpenAI API key

### Initial Setup

```bash
# Clone repository
cd /opt/signout/signout-discord-bot

# Create virtual environment
python3 -m venv .bot-venv
source .bot-venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials
```

### Environment Variables

Create `.env` file:

```bash
# Discord Configuration
DISCORD_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_server_id
DISCORD_ADMIN_CHANNEL_ID=channel_id_for_admin_notifications

# Database Configuration
DB_HOST=192.168.30.151
DB_PORT=5432
DB_NAME=signout_bot
DB_USER=botuser
DB_PASSWORD=your_password

# OpenAI Configuration
OPENAI_API_KEY=sk-your-openai-api-key

# Bot Configuration
CLEANUP_INTERVAL_MINUTES=1
NOTIFICATION_INTERVAL_MINUTES=1
TIMEZONE=America/Chicago
```

### Database Initialization

```bash
# Connect to PostgreSQL
psql -h DB_HOST -U postgres

# Create database and user
CREATE DATABASE signout_bot;
CREATE USER botuser WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE signout_bot TO botuser;

# Tables are auto-created on first run via SQLAlchemy
```

### Running Development Instance

```bash
# Activate virtual environment
source .bot-venv/bin/activate

# Run bot
python mainbot.py

# Bot will:
# 1. Initialize database (create tables if needed)
# 2. Connect to Discord
# 3. Start background tasks
# 4. Sync slash commands
```

### Development Tools

**View logs:**
```bash
# Real-time logging to console when running directly
python mainbot.py

# For systemd service:
sudo journalctl -u test-signout -f
```

**Database access:**
```bash
# Direct PostgreSQL access
psql -h DB_HOST -U botuser -d signout_bot

# Common queries
SELECT * FROM tools;
SELECT * FROM reservations WHERE status = 'ACTIVE';
SELECT * FROM reservation_photos;
```

---

## Database Schema

### Core Tables

#### users
```sql
CREATE TABLE users (
    user_id VARCHAR(50) PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### tools
```sql
CREATE TABLE tools (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    max_time_hours INTEGER NOT NULL DEFAULT 168,
    is_tool_room BOOLEAN DEFAULT FALSE,
    role_id VARCHAR(50),
    role_required BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### reservations
```sql
CREATE TABLE reservations (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) REFERENCES users(user_id),
    tool_id INTEGER REFERENCES tools(id),
    username VARCHAR(100) NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    original_text TEXT,
    formatted_time VARCHAR(200) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    duration_hours FLOAT NOT NULL,
    photo_required BOOLEAN DEFAULT FALSE,
    photo_reminder_sent_at TIMESTAMP,
    photo_warning_sent_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    returned_at TIMESTAMP,
    
    CONSTRAINT check_end_after_start CHECK (end_time > start_time)
);

CREATE INDEX idx_reservations_tool ON reservations(tool_name);
CREATE INDEX idx_reservations_user ON reservations(user_id);
CREATE INDEX idx_reservations_status ON reservations(status);
CREATE INDEX idx_reservations_times ON reservations(start_time, end_time);
```

#### reservation_photos
```sql
CREATE TABLE reservation_photos (
    id SERIAL PRIMARY KEY,
    reservation_id INTEGER REFERENCES reservations(id) ON DELETE SET NULL,
    photo_type VARCHAR(20) NOT NULL,  -- 'START' or 'RETURN'
    photo_url TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT NOW(),
    user_id VARCHAR(50) NOT NULL,
    username VARCHAR(100) NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    reviewed_by_user_id VARCHAR(50),
    reviewed_by_username VARCHAR(100),
    reviewed_at TIMESTAMP,
    approved BOOLEAN,  -- NULL=pending, TRUE=approved, FALSE=rejected
    review_notes TEXT
);

CREATE INDEX idx_photo_reservation ON reservation_photos(reservation_id, photo_type);
CREATE INDEX idx_photo_review_status ON reservation_photos(approved, reviewed_at);
CREATE INDEX idx_photo_uploaded ON reservation_photos(uploaded_at);
```

**Note**: `reservation_id` is nullable and uses `ON DELETE SET NULL`. Photos persist perpetually even after reservations are archived.

#### photo_debts
```sql
CREATE TABLE photo_debts (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    username VARCHAR(100) NOT NULL,
    tool_id INTEGER REFERENCES tools(id),
    tool_name VARCHAR(100) NOT NULL,
    reservation_id INTEGER,  -- Reference to archived reservation
    debt_type VARCHAR(20) NOT NULL,  -- 'START' or 'RETURN'
    due_at TIMESTAMP NOT NULL,
    cleared_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_photo_debts_user ON photo_debts(user_id);
CREATE INDEX idx_photo_debts_active ON photo_debts(user_id) WHERE cleared_at IS NULL;
```

#### reservation_history
```sql
CREATE TABLE reservation_history (
    id SERIAL PRIMARY KEY,
    reservation_id INTEGER NOT NULL,
    user_id VARCHAR(50) NOT NULL,
    username VARCHAR(100) NOT NULL,
    tool_id INTEGER,
    tool_name VARCHAR(100) NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    original_text TEXT,
    formatted_time VARCHAR(200) NOT NULL,
    status VARCHAR(50) NOT NULL,
    photo_urls_json TEXT,  -- JSON array of photo metadata
    duration_hours FLOAT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    returned_at TIMESTAMP,
    archived_at TIMESTAMP DEFAULT NOW(),
    is_admin_block BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_history_user ON reservation_history(user_id);
CREATE INDEX idx_history_tool ON reservation_history(tool_name);
CREATE INDEX idx_history_time ON reservation_history(archived_at);
CREATE INDEX idx_history_admin_block ON reservation_history(is_admin_block);
```

### Relationship Diagram

```
users (1) ──────── (*) reservations
  │                      │
  │                      │
  └─── (*) photo_debts   ├─── (1) tools
                         │
                         └─── (*) reservation_photos
```

### Key Constraints

- **ON DELETE SET NULL** on `reservation_photos.reservation_id`: Photos persist after reservation archival
- **No CASCADE deletes** in SQLAlchemy relationships: Photos are never auto-deleted
- **Status enum** enforced at application level: ACTIVE, EXPIRED, RETURNED, CANCELLED, ADMIN_BLOCK
- **Timezone handling**: Database stores naive UTC datetimes, application handles timezone conversion

---

## Code Structure

### File Organization

```
signout-discord-bot/
├── mainbot.py              # Main bot entry point, command handlers
├── admin_panel.py          # Admin commands (Cog)
├── config.py               # Configuration loader
├── database.py             # SQLAlchemy models
├── db_session.py           # Database session management
├── repositories.py         # Data access layer (repositories)
├── models.py               # Dataclass models
├── notifications.py        # Notification management
├── gptparse.py             # GPT time parsing
├── time_utils.py           # Timezone utilities
├── validation.py           # Input validation
├── discord_utils.py        # Discord helpers
├── exceptions.py           # Custom exceptions
├── autocomplete.py         # Autocomplete handlers
├── requirements.txt        # Python dependencies
├── .env                    # Environment variables (not in git)
├── docs/                   # Documentation
│   ├── ADMIN_QUICKSTART.md
│   ├── USER_QUICKSTART.md
│   ├── TECHNICAL_GUIDE.md
│   └── ...
├── helper_scripts/         # Database migrations, utilities
│   ├── migrate_*.py
│   ├── cleanup_*.py
│   ├── check_*.py
│   └── ...
└── systemd/               # Service files
    ├── test-signout.service
    └── test-signout.timer
```

### Key Modules

**mainbot.py**
- Bot initialization and event handlers
- Core commands: `/signout`, `/returntool`, `/adjusttime`
- Background tasks: `clean_expired_signouts`, `send_notifications`
- DM photo upload handler

**admin_panel.py**
- All `/admin` commands organized in command groups
- Tool management, admin blocks, photo management
- Consecutive limits, user exemptions
- Debug and monitoring commands

**repositories.py**
- Data access layer following Repository pattern
- `ReservationRepository`, `ToolRepository`, `UserRepository`
- `ReservationPhotoRepository`, `PhotoDebtRepository`
- All database queries isolated from business logic

**database.py**
- SQLAlchemy ORM models
- Enums: `ReservationStatusEnum`, `PhotoTypeEnum`, `PhotoDebtTypeEnum`
- Table definitions with indexes and constraints

**gptparse.py**
- `parse_time_with_gpt()`: Future-only time parsing (for reservations)
- `parse_historical_time_range()`: Backward-looking time parsing (for queries)
- `rewrite_reservation_with_gpt()`: Modify existing reservation times

**notifications.py**
- `NotificationManager`: Centralized notification system
- Upcoming reservation reminders (30 min, 15-20 min)
- Expiration warnings
- Photo requirement reminders
- Admin notifications for missing photos

### Design Patterns

**Repository Pattern**
```python
# repositories.py
class ReservationRepository:
    def __init__(self, session: Session):
        self.session = session
    
    def create(self, ...):
        # Create reservation
    
    def get_active_for_tool(self, tool_name: str):
        # Query active reservations
    
    def check_conflicts(self, ...):
        # Check for overlapping reservations
```

**Dependency Injection**
```python
# Usage in command handlers
with get_db_session() as session:
    res_repo = ReservationRepository(session)
    tool_repo = ToolRepository(session)
    # Use repositories
    session.commit()
```

**Command Groups (Discord.py Cogs)**
```python
# admin_panel.py
class AdminPanel(commands.Cog):
    admin_group = app_commands.Group(name="admin", ...)
    
    @admin_group.command(name="block")
    async def block_add(self, interaction, ...):
        # Handle command
```

### Error Handling

**Custom Exceptions**
```python
# exceptions.py
class InvalidToolChannelError(Exception):
    def __init__(self, message: str, user_message: str):
        self.message = message
        self.user_message = user_message
```

**Usage Pattern**
```python
try:
    tool_name = get_tool_from_channel_or_error(channel)
except InvalidToolChannelError as e:
    await interaction.response.send_message(e.user_message, ephemeral=True)
    return
```

---

## Deployment

### Production Environment

**System Requirements:**
- Linux server (Ubuntu 22.04 LTS recommended)
- Python 3.11+
- PostgreSQL 14+
- 1GB RAM minimum (2GB recommended)
- 10GB disk space

### Systemd Service Setup

**Service File:** `/etc/systemd/system/test-signout.service`

```ini
[Unit]
Description=Test Signout Discord Bot (main service)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/signout/signout-discord-bot
EnvironmentFile=/opt/signout/signout-discord-bot/.env

# Wait for database to be ready
ExecStartPre=/bin/sh -c 'for i in 1 2 3 4 5; do /usr/bin/pg_isready -h ${DB_HOST} -p ${DB_PORT} && exit 0 || sleep 5; done; exit 1'

# Start bot
ExecStart=/opt/signout/signout-discord-bot/.bot-venv/bin/python3 /opt/signout/signout-discord-bot/mainbot.py

# Restart on failure
Restart=on-failure
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=test-signout

[Install]
WantedBy=multi-user.target
```

**Timer File (Optional):** `/etc/systemd/system/test-signout.timer`

For scheduled tasks or delayed start.

### Deployment Steps

```bash
# 1. Copy code to server
rsync -av --exclude='.bot-venv' --exclude='__pycache__' \
    ./ user@server:/opt/signout/signout-discord-bot/

# 2. SSH to server
ssh user@server

# 3. Setup virtual environment
cd /opt/signout/signout-discord-bot
python3 -m venv .bot-venv
source .bot-venv/bin/activate
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
nano .env  # Edit with production credentials

# 5. Test database connection
.bot-venv/bin/python3 -c "from db_session import get_db_session; \
    with get_db_session() as s: print('DB OK')"

# 6. Install systemd service
sudo cp systemd/test-signout.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable test-signout
sudo systemctl start test-signout

# 7. Verify service
sudo systemctl status test-signout
sudo journalctl -u test-signout -f
```

### Service Management

```bash
# Start service
sudo systemctl start test-signout

# Stop service
sudo systemctl stop test-signout

# Restart service
sudo systemctl restart test-signout

# View status
sudo systemctl status test-signout

# View logs
sudo journalctl -u test-signout -n 100
sudo journalctl -u test-signout -f  # Follow mode

# View logs since timestamp
sudo journalctl -u test-signout --since "2025-12-16 10:00:00"

# Enable auto-start on boot
sudo systemctl enable test-signout

# Disable auto-start
sudo systemctl disable test-signout
```

### Hot Reload (Code Updates)

```bash
# 1. Pull latest code
cd /opt/signout/signout-discord-bot
git pull

# 2. Update dependencies if needed
source .bot-venv/bin/activate
pip install -r requirements.txt

# 3. Restart service
sudo systemctl restart test-signout

# 4. Verify startup
sudo journalctl -u test-signout -n 20 --no-pager
```

### Environment-Specific Configuration

**Development:**
```bash
# .env
CLEANUP_INTERVAL_MINUTES=5
NOTIFICATION_INTERVAL_MINUTES=5
```

**Production:**
```bash
# .env
CLEANUP_INTERVAL_MINUTES=1
NOTIFICATION_INTERVAL_MINUTES=1
```

---

## Database Migrations

### Migration Strategy

Migrations are handled via helper scripts in `helper_scripts/` directory. Each migration is a standalone Python script that:

1. Connects to database
2. Applies schema changes using raw SQL
3. Migrates existing data if needed
4. Verifies changes

### Running Migrations

```bash
# General pattern
cd /opt/signout/signout-discord-bot
.bot-venv/bin/python3 helper_scripts/migrate_<feature>.py

# Example: Photo table migration
.bot-venv/bin/python3 helper_scripts/migrate_photo_table.py

# Example: Fix photo cascade delete
.bot-venv/bin/python3 helper_scripts/fix_photo_cascade_delete.py
```

### Available Migrations

| Migration | Purpose | File |
|-----------|---------|------|
| Photo Table | Add reservation_photos table | `migrate_photo_table.py` |
| Photo Enforcement | Add photo_required flags | `migrate_photo_enforcement.py` |
| Photo Cascade Fix | Prevent photo deletion on archive | `fix_photo_cascade_delete.py` |
| Consecutive Signouts | Add consecutive limit tracking | `migrate_consecutive_signouts.py` |
| Admin Block Flag | Add is_admin_block to history | `migrate_add_is_admin_block.py` |
| Role Permissions | Add role_id to tools | `migrate_role_permissions.py` |
| Time-Based Reset | Add reset tracking to limits | `migrate_add_time_based_reset.py` |

### Creating New Migrations

**Template:**

```python
#!/usr/bin/env python3
"""
Migration: Add new feature

Changes:
1. Add new column/table
2. Migrate existing data
3. Create indexes
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_session import get_db_session
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_migration():
    """Execute the migration"""
    print("=" * 70)
    print("Migration: Feature Name")
    print("=" * 70)
    
    response = input("Continue? (yes/no): ").strip().lower()
    if response != "yes":
        print("Aborted.")
        return
    
    with get_db_session() as session:
        try:
            # Step 1: Add column
            logger.info("Step 1: Adding column...")
            session.execute(text("""
                ALTER TABLE table_name 
                ADD COLUMN IF NOT EXISTS column_name TYPE;
            """))
            logger.info("  ✓ Column added")
            
            # Step 2: Migrate data
            logger.info("Step 2: Migrating data...")
            session.execute(text("""
                UPDATE table_name SET column_name = value WHERE condition;
            """))
            logger.info("  ✓ Data migrated")
            
            # Step 3: Create index
            logger.info("Step 3: Creating indexes...")
            session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_name ON table_name(column_name);
            """))
            logger.info("  ✓ Indexes created")
            
            # Commit changes
            session.commit()
            logger.info("\nMigration completed successfully!")
            
        except Exception as e:
            session.rollback()
            logger.error(f"\n✗ Error during migration: {e}")
            raise

if __name__ == "__main__":
    run_migration()
```

### Migration Best Practices

1. **Always backup database before migrations**
2. **Test migrations on development database first**
3. **Use `IF NOT EXISTS` and `IF EXISTS` clauses**
4. **Wrap migrations in transactions**
5. **Verify data after migration**
6. **Document what the migration does**
7. **Include rollback procedures if possible**

### Database Backup Before Migration

```bash
# Backup entire database
pg_dump -h DB_HOST -U botuser signout_bot > backup_$(date +%Y%m%d_%H%M%S).sql

# Backup specific table
pg_dump -h DB_HOST -U botuser -t table_name signout_bot > table_backup.sql

# Restore from backup if needed
psql -h DB_HOST -U botuser signout_bot < backup_file.sql
```

---

## Monitoring and Logging

### Application Logging

**Log Levels:**
- CRITICAL: System failures, cannot continue
- ERROR: Operation failed, user impact
- WARNING: Unexpected but handled
- INFO: Normal operations, state changes
- DEBUG: Detailed diagnostic information

**Setting Log Level:**
```python
# In code
logging.basicConfig(level=logging.INFO)

# Via Discord command
/debug logs level level:DEBUG
```

**Log Locations:**

When running as systemd service:
```bash
# View all logs
sudo journalctl -u test-signout

# Follow logs in real-time
sudo journalctl -u test-signout -f

# Last N lines
sudo journalctl -u test-signout -n 100

# Since specific time
sudo journalctl -u test-signout --since "1 hour ago"
sudo journalctl -u test-signout --since "2025-12-16 10:00:00"

# Specific log level
sudo journalctl -u test-signout -p err  # Errors only
```

When running directly:
```bash
# Logs to console
python mainbot.py
```

### Discord Command Logging

**Stream logs to Discord channel:**
```bash
/debug logs watch enable:true
```

Streams logs in real-time to current channel. Useful for troubleshooting without SSH access.

**View recent logs in Discord:**
```bash
/debug logs tail lines:50
```

### Key Metrics to Monitor

**Database Metrics:**
```sql
-- Active reservations count
SELECT COUNT(*) FROM reservations WHERE status = 'ACTIVE';

-- Photo debts count
SELECT COUNT(*) FROM photo_debts WHERE cleared_at IS NULL;

-- Recent errors (if logging to DB)
SELECT * FROM logs WHERE level = 'ERROR' AND created_at > NOW() - INTERVAL '1 hour';
```

**System Metrics:**
```bash
# Service uptime
systemctl status test-signout

# Memory usage
ps aux | grep python | grep mainbot

# Database connections
psql -h DB_HOST -U botuser -c "SELECT count(*) FROM pg_stat_activity WHERE datname='signout_bot';"
```

### Health Checks

**Manual Health Check:**
```bash
# Check bot is running
sudo systemctl is-active test-signout

# Check database connectivity
psql -h DB_HOST -U botuser -d signout_bot -c "SELECT 1;"

# Check Discord connectivity
# Bot should be online in Discord server
```

**Automated Monitoring (Optional):**

Consider setting up:
- Uptime monitoring (Uptime Robot, StatusCake)
- Log aggregation (ELK stack, Grafana Loki)
- Discord webhook for critical errors
- Database monitoring (pgAdmin, Datadog)

---

## Backup and Recovery

### Database Backup

**Automated Daily Backup:**

```bash
#!/bin/bash
# /opt/signout/backups/backup_db.sh

BACKUP_DIR="/opt/signout/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DB_HOST="192.168.30.151"
DB_NAME="signout_bot"
DB_USER="botuser"

# Create backup
pg_dump -h $DB_HOST -U $DB_USER $DB_NAME | gzip > $BACKUP_DIR/signout_bot_$TIMESTAMP.sql.gz

# Keep only last 30 days
find $BACKUP_DIR -name "signout_bot_*.sql.gz" -mtime +30 -delete

echo "Backup completed: signout_bot_$TIMESTAMP.sql.gz"
```

**Cron Schedule:**
```bash
# Edit crontab
crontab -e

# Add daily backup at 2 AM
0 2 * * * /opt/signout/backups/backup_db.sh >> /var/log/signout_backup.log 2>&1
```

### Database Recovery

**Restore from Backup:**

```bash
# Stop bot to prevent writes
sudo systemctl stop test-signout

# Restore database
gunzip < signout_bot_TIMESTAMP.sql.gz | psql -h DB_HOST -U botuser signout_bot

# Verify data
psql -h DB_HOST -U botuser signout_bot -c "SELECT COUNT(*) FROM reservations;"

# Start bot
sudo systemctl start test-signout
```

**Point-in-Time Recovery:**

If using PostgreSQL WAL archiving, consult PostgreSQL documentation for PITR.

### Application Backup

**Code Backup:**
```bash
# Git repository (primary backup)
git push origin main

# Create tarball backup
tar -czf signout_bot_backup_$(date +%Y%m%d).tar.gz \
    --exclude='.bot-venv' \
    --exclude='__pycache__' \
    --exclude='.env' \
    /opt/signout/signout-discord-bot/
```

**Environment Configuration Backup:**
```bash
# Backup .env (store securely, not in git)
cp .env .env.backup.$(date +%Y%m%d)

# Encrypt sensitive backups
gpg -c .env.backup.$(date +%Y%m%d)
```

### Disaster Recovery Plan

1. **Database Failure:**
   - Restore from most recent daily backup
   - Replay any transactions from application logs if needed
   - Verify data integrity before resuming service

2. **Application Failure:**
   - Pull latest code from git
   - Restore .env from backup
   - Recreate virtual environment
   - Restart service

3. **Full System Failure:**
   - Provision new server
   - Install dependencies (Python, PostgreSQL client)
   - Clone git repository
   - Restore database from backup
   - Configure systemd service
   - Start service

**Recovery Time Objective (RTO):** Target 1 hour
**Recovery Point Objective (RPO):** Target 24 hours (daily backups)

---

## Troubleshooting

### Common Issues

#### Bot Not Starting

**Symptoms:**
- Service fails to start
- Immediate crash after start

**Diagnosis:**
```bash
# Check service status
sudo systemctl status test-signout

# View recent logs
sudo journalctl -u test-signout -n 50

# Common errors to look for:
# - ModuleNotFoundError: Missing dependency
# - Database connection errors
# - Invalid Discord token
# - Configuration errors
```

**Solutions:**
```bash
# Install missing dependencies
source .bot-venv/bin/activate
pip install -r requirements.txt

# Verify database connection
psql -h DB_HOST -U botuser signout_bot -c "SELECT 1;"

# Verify .env file
cat .env | grep -v "^#" | grep -v "^$"

# Check token validity
# Token should start with "MTk..." or similar
```

#### Database Connection Failures

**Symptoms:**
- "could not connect to server"
- "password authentication failed"

**Diagnosis:**
```bash
# Test database connectivity
pg_isready -h DB_HOST -p 5432

# Test authentication
psql -h DB_HOST -U botuser -d signout_bot -c "SELECT NOW();"

# Check PostgreSQL is running
systemctl status postgresql  # If local
```

**Solutions:**
```bash
# Fix pg_hba.conf for remote access
# On database server:
sudo nano /etc/postgresql/14/main/pg_hba.conf
# Add: host signout_bot botuser IP_ADDRESS/32 md5

# Fix postgresql.conf for listening
sudo nano /etc/postgresql/14/main/postgresql.conf
# Set: listen_addresses = '*'

# Restart PostgreSQL
sudo systemctl restart postgresql

# Verify password
psql -h DB_HOST -U postgres
ALTER USER botuser PASSWORD 'new_password';
```

#### Discord Commands Not Responding

**Symptoms:**
- Bot online but commands don't work
- "Application did not respond" error

**Diagnosis:**
```bash
# Check if bot is truly connected
sudo journalctl -u test-signout -n 20 | grep "connected to Gateway"

# Check for command sync errors
sudo journalctl -u test-signout -n 100 | grep -i "command"

# Verify bot permissions in Discord
# Bot needs: Send Messages, Use Slash Commands, Embed Links
```

**Solutions:**
```python
# Manually sync commands (in code)
await bot.tree.sync()

# Or restart bot
sudo systemctl restart test-signout

# Check Discord Developer Portal
# - Verify bot token
# - Check bot permissions
# - Verify application ID matches
```

#### Photo Uploads Failing

**Symptoms:**
- Users can't upload photos
- "No active reservations found" error

**Diagnosis:**
```bash
# Check if Tool Room detection working
psql -h DB_HOST -U botuser signout_bot
SELECT name, is_tool_room FROM tools WHERE is_tool_room = true;

# Check photo_required flag on reservations
SELECT id, username, tool_name, photo_required 
FROM reservations 
WHERE status = 'ACTIVE' AND photo_required = true;

# Check for photo debts
SELECT * FROM photo_debts WHERE cleared_at IS NULL;
```

**Solutions:**
```bash
# Verify channel category name is exactly "Tool Room"
# Case-sensitive!

# Manually set is_tool_room flag
psql -h DB_HOST -U botuser signout_bot
UPDATE tools SET is_tool_room = true WHERE name = 'welder1';

# Clear stuck photo debts
DELETE FROM photo_debts WHERE user_id = 'USER_ID';
```

#### Admin Blocks Not Working

**Symptoms:**
- Users can sign out during admin blocks
- Blocks don't show in list

**Diagnosis:**
```bash
# Check if ADMIN_BLOCK reservations exist
psql -h DB_HOST -U botuser signout_bot
SELECT * FROM reservations WHERE status = 'ADMIN_BLOCK';

# Verify conflict checking includes ADMIN_BLOCK
# Check repositories.py check_conflicts() method
```

**Solutions:**
- Ensure `check_conflicts()` includes `ADMIN_BLOCK` status
- Verify timezone handling (naive vs aware datetime)
- Check `list_blocks` queries ADMIN_BLOCK directly

#### Memory Leaks

**Symptoms:**
- Bot memory usage grows over time
- Eventually crashes or slows down

**Diagnosis:**
```bash
# Monitor memory usage over time
watch -n 60 'ps aux | grep mainbot'

# Check for unclosed database sessions
psql -h DB_HOST -U botuser -c "SELECT count(*), state FROM pg_stat_activity WHERE datname='signout_bot' GROUP BY state;"
```

**Solutions:**
```python
# Ensure all database sessions are closed
with get_db_session() as session:
    # Use session
    # Automatically closed on exit

# Check for background task issues
# Verify tasks don't accumulate state
```

### Debug Mode

**Enable verbose logging:**
```bash
# Via Discord
/debug logs level level:DEBUG

# In code
logging.basicConfig(level=logging.DEBUG)
```

**Stream logs to Discord channel:**
```bash
/debug logs watch enable:true
```

**View specific module logs:**
```python
# In code, add module-specific logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
```

### Emergency Procedures

**Bot is stuck/frozen:**
```bash
# Force restart
sudo systemctl restart test-signout

# If that fails, kill process
sudo pkill -9 -f mainbot.py
sudo systemctl start test-signout
```

**Database corruption:**
```bash
# Stop bot
sudo systemctl stop test-signout

# Run PostgreSQL integrity checks
psql -h DB_HOST -U botuser signout_bot
VACUUM FULL ANALYZE;

# Check for corruption
SELECT * FROM pg_stat_database WHERE datname = 'signout_bot';

# Restore from backup if needed
```

**Data loss / rollback needed:**
```bash
# Stop bot
sudo systemctl stop test-signout

# Restore from backup
gunzip < backup.sql.gz | psql -h DB_HOST -U botuser signout_bot

# Start bot
sudo systemctl start test-signout
```

---

## Security

### Access Control

**Database Security:**
- PostgreSQL user has limited privileges (only signout_bot database)
- Password authentication required
- Connection restricted to specific IPs via pg_hba.conf
- No superuser access

**Discord Bot Security:**
- Bot token stored in .env (not in code)
- .env excluded from git via .gitignore
- Admin commands require Discord Administrator permission
- Slash commands have default_permissions set

**Server Security:**
- Bot runs as dedicated user (not root recommended for production)
- File permissions: .env should be 600
- Service file in /etc/systemd owned by root

### Secrets Management

**Environment Variables (.env):**
```bash
# Never commit to git
echo ".env" >> .gitignore

# Restrict file permissions
chmod 600 .env

# Backup encrypted
gpg -c .env
```

**Discord Token:**
- Regenerate if exposed
- Store in environment variable, never hardcode
- Rotate periodically (every 6 months)

**OpenAI API Key:**
- Restrict usage with spending limits in OpenAI dashboard
- Monitor usage for anomalies
- Use separate key for dev/prod

**Database Password:**
- Strong password (16+ characters, mixed case, numbers, symbols)
- Different from any user passwords
- Stored only in .env file

### Input Validation

All user inputs are validated:
```python
# Time input validated by GPT parsing
# Prevents SQL injection via natural language

# User IDs validated as Discord snowflakes
# Tool names sanitized before database queries

# Photo URLs validated as Discord CDN URLs
# No arbitrary URL uploads
```

### SQL Injection Prevention

**Using SQLAlchemy ORM:**
```python
# Safe: Parameterized queries
session.query(ReservationModel).filter(
    ReservationModel.user_id == user_id
).all()

# Avoid: String concatenation
# Bad: session.execute(f"SELECT * FROM reservations WHERE user_id = '{user_id}'")
```

### Rate Limiting

Discord has built-in rate limiting for:
- Command invocations
- Message sending
- API calls

Monitor for rate limit errors:
```bash
sudo journalctl -u test-signout | grep -i "rate limit"
```

### Audit Trail

All admin actions are logged:
```python
logger.info(f"Admin {interaction.user.name} created admin block: {formatted_time}")
logger.info(f"Admin {interaction.user.name} cleared photo debt for {username}")
```

Reservation history preserved:
- All reservations archived to reservation_history table
- Photos persist permanently with metadata
- Photo review decisions tracked with admin name and timestamp

---

## Performance Tuning

### Database Optimization

**Indexes:**
All frequently queried columns have indexes:
```sql
-- Existing indexes
CREATE INDEX idx_reservations_tool ON reservations(tool_name);
CREATE INDEX idx_reservations_user ON reservations(user_id);
CREATE INDEX idx_reservations_status ON reservations(status);
CREATE INDEX idx_reservations_times ON reservations(start_time, end_time);
```

**Query Optimization:**
```python
# Use eager loading for relationships
reservation = session.query(ReservationModel).options(
    joinedload(ReservationModel.photos)
).filter_by(id=res_id).first()

# Batch operations instead of loops
session.bulk_insert_mappings(ReservationModel, data_list)
```

**Connection Pooling:**
SQLAlchemy handles connection pooling automatically. Default pool size is 5, max overflow 10.

**Vacuum and Analyze:**
```bash
# Regular maintenance (weekly)
psql -h DB_HOST -U botuser signout_bot -c "VACUUM ANALYZE;"

# Full vacuum (monthly, during low usage)
psql -h DB_HOST -U botuser signout_bot -c "VACUUM FULL ANALYZE;"
```

### Application Performance

**Background Task Intervals:**
```python
# config.py
CLEANUP_INTERVAL_MINUTES = 1  # Cleanup expired reservations
NOTIFICATION_INTERVAL_MINUTES = 1  # Send notifications
```

Balance between responsiveness and CPU usage. 1 minute is reasonable for most use cases.

**Discord API Optimization:**
- Batch message sends when possible
- Use ephemeral messages to reduce clutter
- Cache guild/role lookups

**GPT API Optimization:**
- Cache common time patterns (optional)
- Use shorter prompts
- Set reasonable max_tokens limit
- Monitor OpenAI API costs

### Memory Management

**Session Management:**
```python
# Always use context manager
with get_db_session() as session:
    # Database operations
    session.commit()
# Session automatically closed
```

**Avoid Memory Leaks:**
- Don't store large objects in global scope
- Clear collections after processing
- Let background tasks complete before new run

### Scaling Considerations

**Current Architecture:**
- Single bot instance
- Single database server
- Suitable for 1-5 Discord servers, 100-1000 users

**Horizontal Scaling (if needed):**
- Shard Discord bot across multiple processes
- Load balance database queries
- Separate read replicas for queries
- Cache layer (Redis) for frequently accessed data

**Vertical Scaling:**
- Increase database resources (CPU, RAM)
- Increase bot server resources
- Optimize queries before scaling hardware

---

## Contributing

### Development Workflow

1. **Fork/Clone Repository:**
```bash
git clone https://github.com/your-org/signout-discord-bot.git
cd signout-discord-bot
```

2. **Create Feature Branch:**
```bash
git checkout -b feature/new-feature-name
```

3. **Make Changes:**
- Write code following existing patterns
- Add logging for important operations
- Update documentation

4. **Test Changes:**
```bash
# Run bot locally
python mainbot.py

# Test commands in Discord
# Verify database changes
```

5. **Commit and Push:**
```bash
git add .
git commit -m "Add feature: description"
git push origin feature/new-feature-name
```

6. **Create Pull Request**

### Code Standards

**Python Style:**
- Follow PEP 8
- Use type hints where possible
- Maximum line length: 120 characters
- Use descriptive variable names

**Documentation:**
- Docstrings for all public functions
- Comments for complex logic
- Update relevant .md files

**Logging:**
```python
# Use appropriate log levels
logger.info("Normal operation")
logger.warning("Unexpected but handled")
logger.error("Operation failed", exc_info=True)
```

**Error Handling:**
```python
# Always catch specific exceptions
try:
    operation()
except SpecificException as e:
    logger.error(f"Failed: {e}")
    await interaction.response.send_message("User-friendly error", ephemeral=True)
```

### Testing

**Manual Testing Checklist:**
- [ ] Bot connects to Discord
- [ ] Database connection works
- [ ] `/signout` creates reservation
- [ ] `/returntool` marks as returned
- [ ] Admin blocks prevent signouts
- [ ] Photo enforcement works in Tool Room
- [ ] Notifications are sent
- [ ] Cleanup task archives old reservations

**Database Testing:**
```bash
# Test on separate database
DB_NAME=signout_bot_test python mainbot.py
```

### Release Process

1. **Version Bump:**
   - Update version in comments/docs
   - Tag release in git

2. **Deployment:**
```bash
# On production server
cd /opt/signout/signout-discord-bot
git pull origin main
source .bot-venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart test-signout
```

3. **Verification:**
```bash
# Check logs
sudo journalctl -u test-signout -n 50

# Test critical commands
# Verify in Discord
```

4. **Rollback Plan:**
```bash
# If issues occur
git checkout previous_commit
sudo systemctl restart test-signout
```

---

## Appendix

### Useful SQL Queries

**Active Reservations:**
```sql
SELECT username, tool_name, formatted_time, status
FROM reservations
WHERE status IN ('ACTIVE', 'ADMIN_BLOCK')
ORDER BY start_time;
```

**Photo Debt Summary:**
```sql
SELECT username, tool_name, debt_type, 
       CASE WHEN cleared_at IS NULL THEN 'ACTIVE' ELSE 'CLEARED' END as status
FROM photo_debts
ORDER BY created_at DESC
LIMIT 20;
```

**User Statistics:**
```sql
SELECT username, 
       COUNT(*) as total_reservations,
       SUM(duration_hours) as total_hours,
       AVG(duration_hours) as avg_duration
FROM reservation_history
GROUP BY username
ORDER BY total_hours DESC;
```

**Tool Usage Statistics:**
```sql
SELECT tool_name,
       COUNT(*) as reservations,
       SUM(duration_hours) as total_hours,
       COUNT(DISTINCT user_id) as unique_users
FROM reservation_history
GROUP BY tool_name
ORDER BY reservations DESC;
```

**Recent Photos:**
```sql
SELECT username, tool_name, photo_type, uploaded_at,
       CASE 
           WHEN approved IS NULL THEN 'PENDING'
           WHEN approved = true THEN 'APPROVED'
           ELSE 'REJECTED'
       END as status
FROM reservation_photos
ORDER BY uploaded_at DESC
LIMIT 20;
```

### Environment Variables Reference

| Variable | Description | Example | Required |
|----------|-------------|---------|----------|
| DISCORD_TOKEN | Bot token from Discord Developer Portal | MTk... | Yes |
| DISCORD_GUILD_ID | Discord server ID | 1234567890 | Yes |
| DISCORD_ADMIN_CHANNEL_ID | Admin notification channel ID | 1234567890 | No |
| DB_HOST | PostgreSQL host | 192.168.30.151 | Yes |
| DB_PORT | PostgreSQL port | 5432 | Yes |
| DB_NAME | Database name | signout_bot | Yes |
| DB_USER | Database user | botuser | Yes |
| DB_PASSWORD | Database password | password | Yes |
| OPENAI_API_KEY | OpenAI API key | sk-... | Yes |
| CLEANUP_INTERVAL_MINUTES | Cleanup task interval | 1 | No |
| NOTIFICATION_INTERVAL_MINUTES | Notification task interval | 1 | No |
| TIMEZONE | Default timezone | America/Chicago | No |

### Discord Permissions Required

| Permission | Purpose |
|------------|---------|
| Send Messages | Send responses to channels |
| Embed Links | Embed formatted messages |
| Attach Files | Send photos in messages |
| Use Slash Commands | Register and respond to commands |
| Read Message History | Process DM photo uploads |
| Manage Roles | Create and assign tool roles |
| View Channels | Access tool channels |

### GPT Prompt Reference

**Time Parsing Prompt (Future Only):**
```
Parse the following user input into a specific time range for a tool reservation.
Current time: {current_time} in Central Time (America/Chicago).

STRICT RULES - CRITICAL:
1. ONLY allow times in the FUTURE
2. REJECT any attempt to book times in the PAST
...
```

**Historical Time Range Prompt:**
```
Parse the following user input into a specific time range for querying historical records.
Current time: {current_time} in Central Time (America/Chicago).

This is for HISTORICAL QUERIES, so past dates are ALLOWED and EXPECTED.
...
```

---

## Support and Contact

**For Technical Issues:**
- Check this guide first
- Review logs: `sudo journalctl -u test-signout`
- Check Discord bot status
- Verify database connectivity

**For Bug Reports:**
- Include: Error message, timestamp, what command was run
- Attach relevant logs
- Describe expected vs actual behavior

**For Feature Requests:**
- Describe use case clearly
- Explain why it's needed
- Consider existing workarounds

---

**Document Version:** 1.0
**Last Updated:** December 16, 2025
**Maintainer:** Technical Team
