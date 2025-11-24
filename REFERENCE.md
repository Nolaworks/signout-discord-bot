# 🚀 Quick Reference Card

## Essential Commands

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Add your tokens

# Migrate data
python migrate_to_db.py

# Run bot
python mainbot.py
```

### File Structure
```
Core Files:
├── mainbot.py          - Main bot (520 lines)
├── admin_panel.py      - Admin commands (782 lines)
├── config.py           - Configuration
├── database.py         - SQLAlchemy models
├── repositories.py     - Data access layer
└── migrate_to_db.py    - Migration script

Utilities:
├── models.py           - Dataclasses
├── time_utils.py       - Time operations
├── discord_utils.py    - Discord helpers
├── validation.py       - Input validation
├── exceptions.py       - Custom exceptions
└── autocomplete.py     - Autocomplete functions

Legacy (can delete after migration):
├── utils.py            - Old utilities
├── tools.json          - Old data
└── history.csv         - Old history

Backups:
├── mainbot_old.py      - Original mainbot
└── admin_panel_old.py  - Original admin panel
```

## User Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/help` | Show help | `/help` |
| `/signout` | Reserve tool | `/signout time:"now for 2 hours"` |
| `/reservations` | List reservations | `/reservations` |
| `/returntool` | Return tool | `/returntool reservation:<autocomplete>` |
| `/comment` | Post comment | `/comment comment:"Laser is acting up"` |

## Admin Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/addtool` | Add tool | `/addtool tool:"new-tool" max_hours:168` |
| `/removetool` | Remove tool | `/removetool tool:"old-tool"` |
| `/maxtime` | Set max time | `/maxtime hours:24` |
| `/clearreservations` | Clear all | `/clearreservations` |
| `/forcereturn` | Force return | `/forcereturn` |
| `/adjusttime` | Adjust time | `/adjusttime old_time:<auto> choice:end new_value:"5pm"` |
| `/adjusttime_admin` | Adjust user's time | `/adjusttime_admin user:"john" ...` |
| `/adblock` | Block all tools | `/adblock time:"now to 2pm" force:false` |
| `/adunblock` | Unblock tools | `/adunblock time:"now to 2pm"` |
| `/listblocks` | List blocks | `/listblocks` |
| `/loglevel` | Set log level | `/loglevel level:"DEBUG"` |
| `/taillogs` | View logs | `/taillogs lines:50` |
| `/watchlogs` | Stream logs | `/watchlogs enable:true` |

## Configuration (.env)

```bash
# Required
DISCORD_TOKEN=your_token_here
OPENAI_API_KEY=your_key_here

# Database (choose one)
DATABASE_URL=DATABASE_URL_REDACTEDlocalhost/signout_bot
# DATABASE_URL=sqlite:///signout_bot.db

# Optional
DEFAULT_MAX_TIME_HOURS=168
REQUIRE_PHOTO_IN_TOOL_ROOM=true
ALLOW_GENERAL_CHAT=false
AUTO_CREATE_TOOLS=true
TIMEZONE=America/Chicago
LOG_LEVEL=INFO
```

## Database Tables

| Table | Purpose |
|-------|---------|
| `users` | User profiles & stats |
| `tools` | Tool definitions |
| `reservations` | Active reservations |
| `reservation_history` | Archived reservations |
| `tool_statistics` | Per-tool metrics |
| `user_statistics` | Per-user metrics |
| `user_tool_statistics` | Per-user-per-tool stats |

## Common Patterns

### Using Repositories
```python
from db_session import get_db_session
from repositories import ToolRepository, ReservationRepository

with get_db_session() as session:
    tool_repo = ToolRepository(session)
    res_repo = ReservationRepository(session)
    
    # Get tool
    tool = tool_repo.get_by_name("laser-cutter")
    
    # Get reservations
    reservations = res_repo.get_active_for_tool("laser-cutter")
    
    # Create reservation
    res_repo.create(
        user_id="123",
        username="john",
        tool_name="laser-cutter",
        start_time=start,
        end_time=end,
        original_text="now for 2 hours",
        formatted_time="11-24-2025 10:00 to 11-24-2025 12:00"
    )
    
    # Auto-commit on success, auto-rollback on error
```

### Parsing Time
```python
from time_utils import parse_time_range, CENTRAL_TZ
from gptparse import parse_time_with_gpt

# Parse natural language
formatted = await parse_time_with_gpt("now for 2 hours")
# Returns: "11-24-2025 10:00 to 11-24-2025 12:00"

# Parse to datetime
start, end = parse_time_range(formatted, CENTRAL_TZ)
```

### Error Handling
```python
from exceptions import InvalidToolChannelError
from discord_utils import get_tool_from_channel_or_error

try:
    tool = get_tool_from_channel_or_error(channel)
except InvalidToolChannelError as e:
    await interaction.response.send_message(
        e.user_message,
        ephemeral=True
    )
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Import errors | `pip install -r requirements.txt` |
| Database connection | Check PostgreSQL is running |
| Migration fails | Check `tools.json` format |
| Commands not syncing | Restart bot, check logs |
| Photo validation error | Check category name or disable in config |

## Useful SQL Queries

```sql
-- Most active users
SELECT username, COUNT(*) as count, SUM(duration_hours) as hours
FROM reservation_history
GROUP BY username
ORDER BY hours DESC
LIMIT 10;

-- Most popular tools
SELECT tool_name, COUNT(*) as count
FROM reservation_history
GROUP BY tool_name
ORDER BY count DESC;

-- Current active reservations
SELECT username, tool_name, formatted_time
FROM reservations
WHERE status = 'active'
ORDER BY start_time;

-- Admin blocks
SELECT tool_name, formatted_time
FROM reservations
WHERE status = 'admin_block'
AND end_time > NOW();
```

## Rollback

```bash
# If something goes wrong
cp mainbot_old.py mainbot.py
cp admin_panel_old.py admin_panel.py
cp tools.json.backup tools.json
python mainbot.py
```

## Performance

```bash
# Monitor bot
tail -f bot.log

# Check database size
du -h signout_bot.db  # SQLite
# or
psql -U botuser -d signout_bot -c "SELECT pg_size_pretty(pg_database_size('signout_bot'));"

# Vacuum database (PostgreSQL)
psql -U botuser -d signout_bot -c "VACUUM ANALYZE;"
```

## Testing Checklist

- [ ] Bot starts without errors
- [ ] User commands work
- [ ] Admin commands work
- [ ] Reservations save to database
- [ ] Conflicts detected
- [ ] Cleanup task runs
- [ ] Photos validate correctly
- [ ] Admin blocks work
- [ ] Logs stream properly

## Next Steps

1. **Test locally** with SQLite
2. **Setup PostgreSQL** for production
3. **Run migration** to transfer data
4. **Deploy** and monitor
5. **Add features** using clean architecture

## Support

- **Architecture**: See `REFACTORING.md`
- **Deployment**: See `DEPLOYMENT.md`
- **Getting Started**: See `QUICKSTART.md`
- **Summary**: See `COMPLETION_SUMMARY.md`

---

**Remember:** Old code is backed up as `*_old.py` files. You can always rollback if needed!

**Status:** ✅ Ready for Production
