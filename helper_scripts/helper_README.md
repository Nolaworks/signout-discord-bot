# Helper Scripts

This directory contains utility scripts for database management, migration, and maintenance.

## Migration Scripts

### `migrate_to_db.py`
**Purpose:** Migrate data from JSON/CSV files to PostgreSQL database

**Usage:**
```bash
python3 helper_scripts/migrate_to_db.py
```

**When to use:**
- Initial setup of database from existing JSON data
- Migrating from file-based storage to database
- Production deployment migration

**What it does:**
- Creates all database tables (including new consecutive signout tables)
- Migrates tools from `tools.json`
- Migrates active reservations
- Migrates historical data from `history.csv`
- Verifies new tables were created

---

### `migrate_consecutive_signouts.py`
**Purpose:** Add consecutive signout tracking tables to existing database

**Usage:**
```bash
python3 helper_scripts/migrate_consecutive_signouts.py
```

**When to use:**
- Adding consecutive signout features to existing database
- Upgrading from older version without these tables

**What it creates:**
- `tool_signout_limits` - Per-tool limit configuration
- `consecutive_signout_tracker` - User signout tracking
- `consecutive_signout_exemptions` - Admin exemptions

---

### `fix_consecutive_tables.py`
**Purpose:** Fix foreign key relationships in consecutive signout tables

**Usage:**
```bash
python3 helper_scripts/fix_consecutive_tables.py
```

**When to use:**
- If you get foreign key errors related to consecutive signout tables
- After manually creating tables without proper foreign keys

**What it does:**
- Drops existing consecutive signout tables
- Recreates them with correct foreign key constraints

---

### `migrate_role_permissions.py`
**Purpose:** Add role-based permission columns to tools table

**Usage:**
```bash
python3 helper_scripts/migrate_role_permissions.py
```

**When to use:**
- Adding role-based permissions to existing database
- Upgrading to version with tool access control

**What it adds:**
- `role_id` column - Stores Discord role ID for tool
- `role_required` column - Whether role is needed to sign out

**After migration:**
- Run `/syncroles` in Discord to create roles for existing tools
- See `ROLE_BASED_PERMISSIONS.md` for full documentation

---

## Database Management

### `reset_db.py`
**Purpose:** Reset the database (DANGER: Deletes all data!)

**Usage:**
```bash
python3 helper_scripts/reset_db.py
```

**⚠️ WARNING:** This will delete ALL data in the database!

**When to use:**
- Development/testing only
- Starting fresh with clean database
- Never use in production without backup

**What it does:**
- Drops all tables
- Recreates empty tables
- Resets database to initial state

---

### `backup_db.sh`
**Purpose:** Create backup of PostgreSQL database

**Usage:**
```bash
./helper_scripts/backup_db.sh
```

**When to use:**
- Before any migration
- Regular scheduled backups
- Before major updates

**What it creates:**
- Timestamped SQL dump file
- Stored in project root

---

## Execution Order for Fresh Setup

1. **Backup existing data** (if any)
   ```bash
   ./helper_scripts/backup_db.sh
   ```

2. **Run migration**
   ```bash
   python3 helper_scripts/migrate_to_db.py
   ```

3. **Verify migration**
   - Check bot logs
   - Test commands
   - Verify data

## Execution Order for Updating Existing Database

1. **Backup database**
   ```bash
   ./helper_scripts/backup_db.sh
   ```

2. **Add new tables**
   ```bash
   python3 helper_scripts/migrate_consecutive_signouts.py
   ```

3. **If foreign key errors occur**
   ```bash
   python3 helper_scripts/fix_consecutive_tables.py
   ```

## Development Only

### Reset Database (Development)
```bash
python3 helper_scripts/reset_db.py
python3 helper_scripts/migrate_to_db.py
```

## Notes

- All scripts should be run from the project root directory
- Activate virtual environment before running: `source .bot-venv/bin/activate`
- Always backup before running any migration or reset script
- Scripts are idempotent where possible (safe to run multiple times)

## Troubleshooting

**ImportError when running scripts:**
```bash
# Make sure you're in the project root and venv is activated
cd /root/signout-discord-bot
source .bot-venv/bin/activate
python3 helper_scripts/script_name.py
```

**Permission denied on backup_db.sh:**
```bash
chmod +x helper_scripts/backup_db.sh
```

**Database connection errors:**
- Check `.env` file has correct `DATABASE_URL`
- Verify PostgreSQL is running
- Check credentials
