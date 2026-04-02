# Production Migration Checklist

## Migration Overview

**Old System (production VM - will be shut down):**
- Flat file storage (tools.json, history.csv)
- No role-based permissions
- No photo system

**New System (this VM - becomes production):**
- PostgreSQL database
- Role-based permissions
- Photo enforcement system

**Strategy:** 
1. Manually copy latest production flat files to this VM
2. Switch Discord token to production bot
3. Reset database (clear test data) and migrate fresh
4. Shut down old production VM

---

## Phase 1: Pre-Migration Preparation

### 1.1 Copy Production Data Files (Manual)
Copy the latest `tools.json` and `history.csv` from the production VM to this server.

```bash
# Create migration directory
mkdir -p /opt/signout/signout-discord-bot/migration_data

# Place files in migration_data/
# - migration_data/tools.json
# - migration_data/history.csv
```

### 1.2 Verify Production Files
```bash
# Check tools.json structure
cat migration_data/tools.json | python3 -m json.tool | head -50

# Check history.csv
wc -l migration_data/history.csv
head -5 migration_data/history.csv
```

### 1.3 Stop Test Bot
```bash
systemctl stop test-signout
```

### 1.4 Update to Production Bot Token
```bash
# Edit .env file to use production bot token
nano /opt/signout/signout-discord-bot/.env

# Change DISCORD_TOKEN to production token
```

### 1.5 Ensure Dependencies
```bash
cd /opt/signout/signout-discord-bot
source .bot-venv/bin/activate
pip install -r requirements.txt
```

---

## Phase 2: Database Reset (Clear Test Data)

### 2.1 Reset Database
```bash
cd /opt/signout/signout-discord-bot
source .bot-venv/bin/activate

# This drops ALL tables and recreates them fresh
python3 helper_scripts/reset_db.py
```

**Expected Output:**
```
WARNING: This will DELETE ALL DATA in the database!
Dropping all tables...
All tables dropped
Creating fresh database schema...
Database schema created successfully
Database reset complete. Ready for migration.
```

### 2.2 Verify Clean State
```bash
psql signout_bot -c "SELECT COUNT(*) FROM tools;"
psql signout_bot -c "SELECT COUNT(*) FROM users;"
# Both should return 0
```

---

## Phase 3: Run Migration

### 3.1 Run Main Migration Script
```bash
cd /opt/signout/signout-discord-bot
source .bot-venv/bin/activate

# Migrate from migration_data directory
python3 scripts/migrate_to_db.py --path /opt/signout/signout-discord-bot/migration_data/
```

**Expected Output:**
```
============================================================
Starting database migration
============================================================
Initializing database schema...
✓ Database schema initialized
Loading tools from migration_data/tools.json
✓ Migrated X tools
✓ Migrated X active reservations
Loading history from migration_data/history.csv
✓ Migrated X history records
Migration completed successfully!
```

### 3.2 Verify Migration
```bash
# Check data counts
psql signout_bot -c "SELECT COUNT(*) as tools FROM tools;"
psql signout_bot -c "SELECT COUNT(*) as users FROM users;"
psql signout_bot -c "SELECT COUNT(*) as history FROM reservation_history;"
psql signout_bot -c "SELECT COUNT(*) as active FROM reservations;"

# Check tool room flags
psql signout_bot -c "SELECT name, is_tool_room FROM tools WHERE is_tool_room = true;"
```

---

## Phase 4: Run Feature Migrations

### 4.1 Role Permissions Migration
```bash
python3 helper_scripts/migrate_role_permissions.py
```

### 4.2 Photo Enforcement Migration
```bash
python3 helper_scripts/migrate_photo_enforcement.py
```

### 4.3 Photo Table Migration
```bash
python3 helper_scripts/migrate_photo_table.py
```

### 4.4 Consecutive Signouts Migration
```bash
python3 helper_scripts/migrate_consecutive_signouts.py
```

### 4.5 Verify All Tables
```bash
psql signout_bot -c "\dt"
```

Should include:
- tools, users, reservations, reservation_history
- reservation_photos, photo_debts
- notification_preferences, notification_log, waitlist
- tool_signout_limits, consecutive_signout_tracker, consecutive_signout_exemptions

---

## Phase 5: Start Production Bot

### 5.1 Create Production Service
```bash
# Copy and modify service file
sudo cp /opt/signout/signout-discord-bot/systemd/test-signout.service /etc/systemd/system/signout-bot.service

# Edit to update description (optional)
sudo nano /etc/systemd/system/signout-bot.service

sudo systemctl daemon-reload
sudo systemctl enable signout-bot
sudo systemctl start signout-bot
```

### 5.2 Verify Bot Is Running
```bash
systemctl status signout-bot
journalctl -u signout-bot -f
```

### 5.3 Shut Down Old Production VM
Once confirmed working, shut down the old production VM.

---

## Phase 6: Configure Roles & Permissions

### 6.1 Get Discord Role IDs
In Discord, enable Developer Mode (User Settings → Advanced → Developer Mode)
Right-click on each role and "Copy ID"

### 6.2 Set Tool Room Permissions
Use bot admin commands:
```
/setrole role:@ToolRoomCertified
```

Or via psql:
```bash
psql signout_bot -c "UPDATE tools SET role_id = '123456789', role_required = true WHERE is_tool_room = true;"
```

---

## Phase 7: Post-Migration Verification

### 7.1 Test Basic Commands
- [ ] `/help` - Shows command list
- [ ] `/reservations` - Shows any migrated active reservations
- [ ] `/signout time:now for 1 hour` - Create test reservation
- [ ] `/returntool` - Return with photo requirement (if Tool Room)
- [ ] `/cancel` - Cancel reservation

### 7.2 Test Admin Commands
- [ ] `/adminhelp` - Shows admin commands
- [ ] `/setrole` - Configure role requirements
- [ ] `/toolstatus` - View tool configuration
- [ ] `/viewphotodebts` - Check photo debt system
- [ ] `/setresignoutlimit` - Configure limits

### 7.3 Test Photo System
- [ ] DM photo upload works
- [ ] Photo debt creation works
- [ ] `/clearphotodebt` works

### 7.4 Test Notifications
- [ ] Reservation reminders send
- [ ] Expiration warnings send
- [ ] Check logs for notification task

---

## Rollback Plan

### If Migration Fails After DB Reset:

1. **Re-run migration** with corrected files
   ```bash
   python3 helper_scripts/reset_db.py
   python3 scripts/migrate_to_db.py --path /path/to/correct/files/
   ```

### If New Bot Has Issues:

1. **Stop new bot**
   ```bash
   systemctl stop signout-bot
   ```

2. **Restart old bot** (on production VM)
   ```bash
   systemctl start discord-bot
   ```

3. **Debug issues** with new bot using test service
   ```bash
   systemctl start test-signout
   journalctl -u test-signout -f
   ```

---

## Quick Reference: Migration Commands

```bash
# Full migration sequence
cd /opt/signout/signout-discord-bot
source .bot-venv/bin/activate

# 1. Reset database
python3 helper_scripts/reset_db.py

# 2. Main migration
python3 scripts/migrate_to_db.py --path ./migration_data/

# 3. Feature migrations
python3 helper_scripts/migrate_role_permissions.py
python3 helper_scripts/migrate_photo_enforcement.py
python3 helper_scripts/migrate_photo_table.py
python3 helper_scripts/migrate_consecutive_signouts.py

# 4. Start bot
systemctl start signout-bot
```

---

## Support

Check logs for issues:
```bash
journalctl -u signout-bot -f
tail -f /opt/signout/signout-discord-bot/bot.log
```
2. Check database: `psql signout_bot`
3. Verify foreign keys: See PRODUCTION_MIGRATION_CHECKLIST.md
4. Contact developer with error logs

## Timeline Estimate

- Backup: 5 minutes
- Code update: 2 minutes
- Migration: 5-10 minutes
- Testing: 10 minutes
- **Total downtime: ~20-25 minutes**

## Notes

- Migration is **idempotent** - safe to run multiple times
- Existing data is preserved
- JSON/CSV files remain as backup
- New tables start empty (no pre-existing limits)
- Admins must manually configure limits after migration
