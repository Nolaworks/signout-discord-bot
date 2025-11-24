# Production Migration Checklist

## Pre-Migration Steps

### 1. Backup Everything
```bash
# Backup database
pg_dump signout_bot > signout_bot_backup_$(date +%Y%m%d_%H%M%S).sql

# Backup JSON/CSV files
cp tools.json tools.json.backup_$(date +%Y%m%d_%H%M%S)
cp history.csv history.csv.backup_$(date +%Y%m%d_%H%M%S)
cp settings.json settings.json.backup_$(date +%Y%m%d_%H%M%S)
```

### 2. Stop the Bot
```bash
# Stop the running bot process
pkill -f mainbot.py
# or
systemctl stop discord-bot  # if using systemd
```

### 3. Pull Latest Code
```bash
cd /path/to/signout-discord-bot
git pull origin main  # or your branch name
```

### 4. Update Dependencies
```bash
source .bot-venv/bin/activate
pip install -r requirements.txt
```

## Migration Steps

### 5. Run Migration Script
```bash
python3 helper_scripts/migrate_to_db.py
```

**Expected Output:**
```
============================================================
Starting database migration
============================================================
Initializing database schema...
✓ Database schema initialized
✓ New table created: tool_signout_limits
✓ New table created: consecutive_signout_tracker
✓ New table created: consecutive_signout_exemptions
Starting migration of tools and reservations...
...
Migration completed successfully!
```

### 6. Verify Migration

**Check Tables Exist:**
```bash
psql signout_bot -c "\dt"
```

Should show:
- consecutive_signout_exemptions
- consecutive_signout_tracker
- tool_signout_limits
- (plus all existing tables)

**Check Data Migrated:**
```bash
psql signout_bot -c "SELECT COUNT(*) FROM tools;"
psql signout_bot -c "SELECT COUNT(*) FROM reservations;"
psql signout_bot -c "SELECT COUNT(*) FROM reservation_history;"
```

### 7. Test Bot Startup
```bash
# Test run (will show any errors)
python3 mainbot.py
```

Watch for:
- ✓ No SQLAlchemy errors
- ✓ "Bot ready! Logged in as..." message
- ✓ No foreign key errors
- ✓ Commands sync successfully

Press Ctrl+C to stop test run.

## Post-Migration Steps

### 8. Start Bot in Production
```bash
# If using systemd:
systemctl start discord-bot
systemctl status discord-bot

# If using screen/tmux:
screen -S discord-bot
python3 mainbot.py
# Ctrl+A, D to detach

# If using nohup:
nohup python3 mainbot.py > bot.log 2>&1 &
```

### 9. Verify Functionality

**Test Basic Commands:**
1. `/help` - Should show updated help with new commands
2. `/reservations` - Should show existing reservations
3. `/signout time:now for 1 hour` - Create a test reservation
4. `/cancel` - Cancel the test reservation

**Test Admin Commands:**
1. `/adminhelp` - Should show new re-signout limit commands
2. `/viewresignoutlimits` - Should return "No limits configured" (initially)
3. `/setresignoutlimit max_consecutive:3 cooldown_hours:24` - Set a test limit
4. `/viewresignoutlimits` - Should show the limit you just set

### 10. Configure Limits (Optional)

For high-demand tools:
```
# In each tool channel:
/setresignoutlimit max_consecutive:3 cooldown_hours:24
```

### 11. Monitor Logs
```bash
tail -f bot.log

# Watch for:
# - No errors
# - Successful command executions
# - Notification checks running
```

## Rollback Plan (If Needed)

### If Migration Fails:

1. **Stop the bot**
   ```bash
   pkill -f mainbot.py
   ```

2. **Restore database**
   ```bash
   psql signout_bot < signout_bot_backup_TIMESTAMP.sql
   ```

3. **Restore files**
   ```bash
   cp tools.json.backup_TIMESTAMP tools.json
   cp history.csv.backup_TIMESTAMP history.csv
   ```

4. **Checkout previous code version**
   ```bash
   git checkout HEAD~1  # or specific commit
   ```

5. **Restart bot**

## New Features Available After Migration

### For Users:
- Cancellations don't count against consecutive signout limits
- Improved fairness for high-demand tools

### For Admins:
- **`/setresignoutlimit`** - Configure max consecutive signouts per tool
- **`/viewresignoutlimits`** - View all configured limits
- **`/checkcooldowns`** - See who's in cooldown
- **`/clearcooldown`** - Reset a user's cooldown
- **`/exemptuser`** - Exempt specific users from limits
- **`/removeexemption`** - Remove user exemptions
- **`/listexemptions`** - View all exempt users

## Verification Checklist

- [ ] Database backup created
- [ ] Code pulled from repository
- [ ] Migration script ran successfully
- [ ] New tables exist in database
- [ ] Data migrated correctly
- [ ] Bot starts without errors
- [ ] Basic commands work
- [ ] Admin commands work
- [ ] Notifications still functioning
- [ ] Logs show no errors
- [ ] Users can create/cancel reservations

## Support

If issues occur:
1. Check logs: `tail -f bot.log`
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
