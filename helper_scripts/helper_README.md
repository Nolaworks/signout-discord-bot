# Helper Scripts

Utility scripts for database management, migration, and maintenance.

## Main Migration Script

### `migrate_to_db.py`

Migrate data from JSON/CSV files to PostgreSQL database.

**Usage:**
```bash
python3 helper_scripts/migrate_to_db.py [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--path <dir>` | Use specified directory for source files |
| `--archive` | Use archive/ directory (auto-detected if exists) |
| `--reset` | Clear all database tables before migration |
| `--help` | Show help message |

**Examples:**

```bash
# Fresh migration from archive directory
python3 helper_scripts/migrate_to_db.py --reset --path archive/

# Add to existing data
python3 helper_scripts/migrate_to_db.py --path archive/

# Auto-detect archive directory
python3 helper_scripts/migrate_to_db.py
```

**What it does:**
1. Initializes database schema (creates all tables)
2. Optionally resets database if `--reset` flag used
3. Migrates tools from `tools.json`
4. Migrates active reservations
5. Migrates historical data from `history.csv`
6. Syncs user and tool statistics

**Source files expected:**
- `tools.json` - Tool definitions and active reservations
- `history.csv` - Historical reservation records

---

## Statistics Sync

### `../scripts/sync_user_statistics.py`

Recalculate and sync statistics from reservation history data.

**Usage:**
```bash
# Dry run (shows what would change)
python3 scripts/sync_user_statistics.py

# Apply changes
python3 scripts/sync_user_statistics.py --apply

# Sync single user only
python3 scripts/sync_user_statistics.py --apply --user USER_ID
```

**What it syncs:**

| Table | Fields Updated |
|-------|----------------|
| `users` | `total_reservations`, `total_time_hours` |
| `user_statistics` | `active_reservations`, `total_reservations`, `total_hours_reserved`, `average_duration_hours`, `most_used_tool_*` |
| `user_tool_statistics` | Per-tool reservation counts and hours for each user |
| `tools` | `total_reservations`, `total_time_hours` |
| `tool_statistics` | `active_reservations`, `total_reservations`, `total_hours_reserved`, `average_duration_hours`, `most_frequent_user_*` |

**What it does NOT sync:**
- `is_admin` field - Determined in real-time from Discord roles
- Tool role assignments - Managed via `/admin role` commands
- Consecutive signout counters - Tracked separately during reservations

---

## Database Backup

### `backup_db.sh`

Create backup of PostgreSQL database.

**Usage:**
```bash
./helper_scripts/backup_db.sh
```

**What it creates:**
- Timestamped SQL dump file in project root
- Format: `backup_YYYYMMDD_HHMMSS.sql`

---

## Quick Reference

### Fresh Installation

```bash
# 1. Setup environment
python3 -m venv .bot-venv
source .bot-venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with credentials

# 3. Initialize database
python3 helper_scripts/migrate_to_db.py

# 4. Start bot
python3 mainbot.py
```

### Migrating from Production JSON/CSV

```bash
# 1. Copy production data
mkdir -p archive
scp production:/path/to/tools.json archive/
scp production:/path/to/history.csv archive/

# 2. Backup existing database (if any)
./helper_scripts/backup_db.sh

# 3. Run migration with reset
python3 helper_scripts/migrate_to_db.py --reset --path archive/

# 4. Sync statistics
python3 scripts/sync_user_statistics.py --apply

# 5. Start bot
python3 mainbot.py
```

### Adding Data to Existing Database

```bash
# 1. Backup first
./helper_scripts/backup_db.sh

# 2. Run migration (no reset)
python3 helper_scripts/migrate_to_db.py --path /path/to/data/

# 3. Sync statistics
python3 scripts/sync_user_statistics.py --apply
```

---

## Troubleshooting

**ImportError when running scripts:**
```bash
# Run from project root with venv activated
cd /opt/signout/signout-discord-bot
source .bot-venv/bin/activate
python3 helper_scripts/migrate_to_db.py
```

**Permission denied on shell scripts:**
```bash
chmod +x helper_scripts/backup_db.sh
```

**Database connection errors:**
- Check `.env` has correct `DATABASE_URL`
- Verify PostgreSQL is running
- Verify credentials are correct

**Duplicate key errors during migration:**
- Use `--reset` flag for fresh migration
- Or manually clear conflicting records

---

## Notes

- Always backup before running migrations
- Scripts should be run from project root directory
- Activate virtual environment before running
- Migration is idempotent for tools (updates existing)
- User merging happens automatically on first bot use
