# Deployment Guide: Refactored Discord Bot

## 🎉 Refactoring Complete!

All code has been successfully refactored to use PostgreSQL with SQLAlchemy. The old files have been backed up as `mainbot_old.py` and `admin_panel_old.py`.

## What Changed

### Files Refactored
- `mainbot.py` - Completely refactored (520 lines)
- `admin_panel.py` - Completely refactored (782 lines)
- `autocomplete.py` - Updated to use database
- `utils.py` - Split into specialized modules (can now be deleted)

### New Architecture
- All commands now use `get_db_session()` context manager
- Repository pattern for all database operations
- Type-safe dataclasses instead of dicts
- Centralized utilities (no duplication)
- Proper error handling with custom exceptions
- Comprehensive logging

## Pre-Deployment Checklist

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

Should install:
- SQLAlchemy 2.0.23
- psycopg2-binary 2.9.9
- alembic 1.13.1
- (plus all existing dependencies)

### 2. Setup Database

#### Option A: PostgreSQL (Production)
```bash
# Install PostgreSQL
sudo apt update
sudo apt install postgresql postgresql-contrib

# Create database and user
sudo -u postgres psql

postgres=# CREATE DATABASE signout_bot;
postgres=# CREATE USER botuser WITH PASSWORD 'your_secure_password';
postgres=# GRANT ALL PRIVILEGES ON DATABASE signout_bot TO botuser;
postgres=# \q
```

#### Option B: SQLite (Testing)
No setup needed! Just configure `.env` file.

### 3. Configure Environment

```bash
# Copy example file
cp .env.example .env

# Edit .env file
nano .env
```

**Required settings**:
```bash
# Discord
DISCORD_TOKEN=your_actual_token_here

# OpenAI
OPENAI_API_KEY=your_actual_key_here

# Database (choose one):
# For PostgreSQL:
DATABASE_URL=DATABASE_URL_REDACTEDlocalhost:5432/signout_bot

# For SQLite (testing):
# DATABASE_URL=sqlite:///signout_bot.db
```

### 4. Backup Existing Data

```bash
# The migration script will create backups, but do manual backup too:
cp tools.json tools.json.backup_manual
cp history.csv history.csv.backup_manual
```

### 5. Run Migration

```bash
# This will:
# - Create all database tables
# - Migrate tools from tools.json
# - Migrate active reservations
# - Migrate history from history.csv

python migrate_to_db.py
```

Expected output:
```
INFO - Initializing database: postgresql://...
INFO - Database initialized successfully
INFO - Starting migration of tools and reservations...
INFO - Migrated tool: laser-cutter
INFO - Migrated tool: 3d-printer
...
INFO - Migration complete: X tools, Y reservations
```

### 6. Verify Migration

```python
# Quick verification script
from db_session import get_db_session
from repositories import ToolRepository, ReservationRepository

with get_db_session() as session:
    tool_repo = ToolRepository(session)
    res_repo = ReservationRepository(session)
    
    tools = tool_repo.get_all()
    print(f"Migrated {len(tools)} tools")
    
    for tool in tools:
        reservations = res_repo.get_active_for_tool(tool.name)
        print(f"  - {tool.name}: {len(reservations)} active reservations")
```

### 7. Test the Bot

```bash
# Run the bot
python mainbot.py
```

Expected startup log:
```
INFO - Initializing database...
INFO - Database initialized successfully
INFO - Loading admin panel...
INFO - Syncing command tree...
INFO - Commands synced: X commands available
INFO - Starting cleanup task...
INFO - Bot ready! Logged in as YourBot#1234
```

## Testing Checklist

Test each command to ensure everything works:

### User Commands
- [ ] `/help` - Display help message
- [ ] `/signout time:"now for 2 hours"` - Create reservation
- [ ] `/reservations` - List reservations
- [ ] `/returntool` - Return a tool
- [ ] `/comment comment:"test message"` - Post comment

### Admin Commands
- [ ] `/addtool tool:"test-tool"` - Add a tool
- [ ] `/maxtime hours:24` - Set max time
- [ ] `/clearreservations` - Clear reservations
- [ ] `/forcereturn` - Force return
- [ ] `/adjusttime` - Adjust reservation time
- [ ] `/adblock time:"now to 2pm"` - Block all tools
- [ ] `/adunblock time:"now to 2pm"` - Unblock tools
- [ ] `/listblocks` - List admin blocks
- [ ] `/loglevel level:"DEBUG"` - Set log level
- [ ] `/taillogs lines:50` - View logs
- [ ] `/watchlogs enable:true` - Stream logs

## Troubleshooting

### Issue: Import Errors
```
ImportError: cannot import name 'X' from 'Y'
```

**Solution**: Make sure all dependencies are installed
```bash
pip install -r requirements.txt --upgrade
```

### Issue: Database Connection Error
```
sqlalchemy.exc.OperationalError: could not connect to server
```

**Solution**: 
- Check PostgreSQL is running: `sudo systemctl status postgresql`
- Verify credentials in `.env`
- Test connection: `psql -U botuser -d signout_bot -h localhost`

### Issue: Migration Fails
```
Error migrating tool X: ...
```

**Solution**:
- Check `tools.json` format is valid
- Review logs for specific error
- Run with `LOG_LEVEL=DEBUG` for more details
- Restore from backup and retry

### Issue: Bot Commands Not Working
```
This command must be used in a 'signout-[tool]' channel
```

**Solution**:
- Ensure channel names start with `signout-`
- Tools are automatically created from channel names
- Or manually add with `/addtool`

### Issue: Photo Validation Error
```
Photo required in Tool Room
```

**Solution**:
- Ensure channel is in "Tool Room" category OR
- Disable photo requirement: `REQUIRE_PHOTO_IN_TOOL_ROOM=false` in `.env`

## Rollback Procedure

If something goes wrong:

```bash
# 1. Stop the bot
# Press Ctrl+C or kill the process

# 2. Restore old code
cp mainbot_old.py mainbot.py
cp admin_panel_old.py admin_panel.py

# 3. Restore old data (if needed)
cp tools.json.backup_manual tools.json
cp history.csv.backup_manual history.csv

# 4. Restart bot with old code
python mainbot.py
```

## Performance Tips

### For Production

1. **Use PostgreSQL** (not SQLite)
   ```bash
   DATABASE_URL=DATABASE_URL_REDACTEDlocalhost:5432/signout_bot
   ```

2. **Enable connection pooling** (automatic with PostgreSQL)

3. **Set appropriate log level**
   ```bash
   LOG_LEVEL=INFO  # or WARNING for production
   ```

4. **Monitor database size**
   ```sql
   SELECT 
       schemaname,
       tablename,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename))
   FROM pg_tables
   WHERE schemaname = 'public'
   ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
   ```

5. **Archive old history periodically**
   ```sql
   -- Archive reservations older than 1 year
   DELETE FROM reservation_history 
   WHERE archived_at < NOW() - INTERVAL '1 year';
   ```

## Monitoring

### Check Bot Health
```bash
# View logs
tail -f bot.log

# Check active reservations
python -c "
from db_session import get_db_session
from repositories import ReservationRepository
from database import ReservationStatusEnum
with get_db_session() as s:
    r = ReservationRepository(s)
    print(f'Active: {len(r.session.query(...).filter(...).all())}')
"
```

### Database Queries

```sql
-- Most active users
SELECT username, COUNT(*) as reservations, SUM(duration_hours) as total_hours
FROM reservation_history
GROUP BY username
ORDER BY total_hours DESC
LIMIT 10;

-- Most popular tools
SELECT tool_name, COUNT(*) as reservations
FROM reservation_history
GROUP BY tool_name
ORDER BY reservations DESC;

-- Current active reservations
SELECT username, tool_name, formatted_time
FROM reservations
WHERE status = 'active'
ORDER BY start_time;
```

## Maintenance

### Regular Tasks

1. **Weekly**: Review logs for errors
2. **Monthly**: Check database size and archive old data
3. **Quarterly**: Update dependencies
4. **Yearly**: Review and optimize queries

### Backup Strategy

```bash
# Daily automated backup (add to crontab)
0 2 * * * pg_dump -U botuser signout_bot > /backups/signout_bot_$(date +\%Y\%m\%d).sql

# Keep last 30 days
find /backups -name "signout_bot_*.sql" -mtime +30 -delete
```

## Next Steps

Now that the refactoring is complete, you can:

1. **Add new features** easily with clean architecture
2. **Implement analytics** using statistics tables
3. **Add notifications** for reservation reminders
4. **Create web dashboard** using the same database
5. **Add unit tests** with the testable repository pattern
6. **Scale to multiple servers** with PostgreSQL

## Support

If you encounter issues:
1. Check logs: `tail -f bot.log`
2. Review `REFACTORING.md` for architecture details
3. Check database connection and credentials
4. Verify all environment variables are set

## Success Indicators

You'll know everything is working when:
- Bot starts without errors
- Commands respond correctly
- Reservations are saved to database
- Conflicts are detected properly
- Cleanup task runs every minute
- Admin commands work
- Logs show normal operation

**Congratulations! Your bot is now running on a robust, scalable architecture! 🚀**
