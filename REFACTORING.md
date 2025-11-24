# Code Refactoring & Database Migration Guide

## Overview

This document outlines the refactoring work completed to improve code quality, remove redundancy, and prepare for PostgreSQL database migration.

## Phase 1: Code Refactoring ✅ COMPLETED

### New File Structure

```
signout-discord-bot/
├── models.py              # Data models (dataclasses)
├── database.py            # SQLAlchemy database models
├── db_session.py          # Database session management
├── repositories.py        # Repository pattern for data access
├── config.py              # Centralized configuration
├── exceptions.py          # Custom exception classes
├── time_utils.py          # Time parsing and manipulation
├── discord_utils.py       # Discord-specific utilities
├── file_utils.py          # File I/O operations (JSON/CSV)
├── validation.py          # Input validation
├── autocomplete.py        # Discord autocomplete functions
├── migrate_to_db.py       # Migration script
├── gptparse.py            # OpenAI time parsing (unchanged)
├── mainbot.py             # Main bot (to be updated)
├── admin_panel.py         # Admin commands (to be updated)
└── utils.py               # Old utils (to be deprecated)
```

### Key Improvements

#### 1. **Type Safety with Dataclasses**
- Created `User`, `Tool`, `Reservation`, `ReservationHistory` dataclasses
- Methods like `is_active()`, `overlaps()`, `duration_hours()`
- Clear, self-documenting code

#### 2. **Time Utilities Centralization**
- All datetime operations in `time_utils.py`
- Functions: `parse_time_range()`, `check_overlap()`, `validate_time_range()`
- Consistent timezone handling (Central TZ)
- Standardized format: `MM-DD-YYYY HH:MM`

#### 3. **Configuration Management**
- All settings in `config.py`
- Environment variable support
- Feature flags for easy toggles
- `.env.example` template provided

#### 4. **Error Handling**
- Custom exception hierarchy in `exceptions.py`
- User-friendly error messages
- Proper error propagation

#### 5. **Modular Utilities**
- **discord_utils.py**: Discord-specific operations
  - `extract_tool_from_channel()`
  - `user_is_admin()`
  - `validate_photo_requirement()`
  
- **file_utils.py**: JSON/CSV operations (backward compatibility)
  - `load_tools()`, `save_tools()`
  - `save_expired_to_csv()`
  - `backup_json_file()`
  
- **validation.py**: Input validation
  - `validate_tool_name()`
  - `validate_time_input()`
  - `sanitize_input()`

#### 6. **Autocomplete Separation**
- Moved autocomplete functions to `autocomplete.py`
- Reusable across commands
- Cleaner command files

## Phase 2: Database Schema Design ✅ COMPLETED

### Database Models

#### **users** table
```sql
- id (PK)
- user_id (unique, indexed) -- Discord user ID
- username (indexed)
- display_name
- is_admin
- total_reservations
- total_time_hours
- created_at, updated_at, last_seen_at
```

#### **tools** table
```sql
- id (PK)
- name (unique, indexed)
- max_time_hours
- channel_id, channel_name
- is_tool_room
- total_reservations
- total_time_hours
- created_at, updated_at
```

#### **reservations** table
```sql
- id (PK)
- user_id (FK to users, indexed)
- tool_id (FK to tools, indexed)
- username, tool_name (denormalized for performance)
- start_time, end_time (indexed)
- original_text, formatted_time
- status (enum: active, expired, returned, cancelled, admin_block)
- photo_url
- duration_hours
- created_at, updated_at, returned_at
```

#### **reservation_history** table
```sql
- id (PK)
- reservation_id (original reservation ID)
- user_id, username, tool_id, tool_name
- start_time, end_time, original_text, formatted_time
- status, photo_url, duration_hours
- created_at, returned_at, archived_at
```

#### **tool_statistics** table
```sql
- id (PK)
- tool_id (FK, unique)
- tool_name
- active_reservations, total_reservations
- total_hours_reserved, average_duration_hours
- most_frequent_user_id, most_frequent_username
- most_frequent_user_count
- updated_at
```

#### **user_statistics** table
```sql
- id (PK)
- user_id (FK, unique)
- username
- active_reservations, total_reservations
- total_hours_reserved, average_duration_hours
- most_used_tool_id, most_used_tool_name
- most_used_tool_count
- updated_at
```

#### **user_tool_statistics** table
```sql
- id (PK)
- user_id (FK), tool_id (FK)
- username, tool_name
- total_reservations, total_hours
- average_duration_hours
- first_reservation_at, last_reservation_at
- updated_at
```

### Indexes & Constraints

- **Composite indexes** for common queries
- **Check constraints** for data integrity
- **Foreign key relationships** with proper cascading
- **Status enums** for type safety

### Repository Pattern

All database operations abstracted through repositories:
- `UserRepository`
- `ToolRepository`
- `ReservationRepository`
- `ReservationHistoryRepository`
- `StatisticsRepository`

Benefits:
- Testable business logic
- Easy to mock for testing
- Swap implementations without changing command code
- Transaction management in one place

## Phase 3: Migration Process

### Prerequisites

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure database**:
   - Copy `.env.example` to `.env`
   - Set `DATABASE_URL` for PostgreSQL:
     ```
     DATABASE_URL=DATABASE_URL_REDACTEDlocalhost:5432/signout_bot
     ```
   - Or use SQLite for testing:
     ```
     DATABASE_URL=sqlite:///signout_bot.db
     ```

3. **Backup existing data**:
   ```bash
   cp tools.json tools.json.backup
   cp history.csv history.csv.backup
   ```

### Running the Migration

```bash
python migrate_to_db.py
```

This script will:
1. Create all database tables
2. Migrate tools from `tools.json`
3. Migrate active reservations
4. Migrate historical data from `history.csv`
5. Create automatic backups with timestamps

### Verification

After migration, verify:
```python
from db_session import get_db_session
from repositories import ToolRepository, ReservationRepository

with get_db_session() as session:
    tool_repo = ToolRepository(session)
    res_repo = ReservationRepository(session)
    
    # Check tools
    tools = tool_repo.get_all()
    print(f"Migrated {len(tools)} tools")
    
    # Check reservations
    for tool in tools:
        reservations = res_repo.get_active_for_tool(tool.name)
        print(f"{tool.name}: {len(reservations)} active reservations")
```

## Phase 4: Code Updates (IN PROGRESS)

### Next Steps

1. **Update mainbot.py**
   - Replace `load_tools()`/`save_tools()` with repository calls
   - Use `get_db_session()` context manager
   - Update `/signout` command
   - Update `/returntool` command
   - Update `/reservations` command
   - Update cleanup task

2. **Update admin_panel.py**
   - Replace dict operations with repository methods
   - Use dataclasses instead of dicts
   - Update all admin commands
   - Leverage statistics tables for new features

3. **Remove redundancy**
   - Delete duplicate time parsing code
   - Consolidate conflict checking
   - Remove old `utils.py` after migration

4. **Add new features enabled by database**
   - Statistics dashboards
   - Advanced querying
   - Trend analysis
   - Better reporting

## Benefits of Refactoring

### Before
```python
# Old code - dict-based, error-prone
data = load_tools()
for r in data["tools"][tool]["reservations"]:
    if " to " in r["time"]:
        s, e = r["time"].split(" to ")
        start = datetime.strptime(s, "%m-%d-%Y %H:%M")
        # ...repeated everywhere...
```

### After
```python
# New code - type-safe, reusable
with get_db_session() as session:
    res_repo = ReservationRepository(session)
    reservations = res_repo.get_active_for_tool(tool_name)
    for res in reservations:
        if res.overlaps(new_reservation):
            # Clean, readable logic
```

### Key Advantages

1. **Type Safety**: Catch errors at write-time, not runtime
2. **DRY Principle**: No code duplication
3. **Testability**: Easy to unit test with mocks
4. **Performance**: Database indexes, query optimization
5. **Scalability**: Supports multiple servers, concurrent users
6. **Maintainability**: Clear structure, easy to onboard
7. **Features**: Statistics, analytics, advanced queries
8. **Reliability**: ACID transactions, data integrity

## Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_TOKEN` | Production Discord bot token | Required |
| `TEST_DISCORD_TOKEN` | Test Discord bot token | Required |
| `OPENAI_API_KEY` | OpenAI API key | Required |
| `DATABASE_URL` | Database connection string | `sqlite:///signout_bot.db` |
| `DEFAULT_MAX_TIME_HOURS` | Default max reservation hours | `168` |
| `REQUIRE_PHOTO_IN_TOOL_ROOM` | Require photos in Tool Room | `true` |
| `ALLOW_GENERAL_CHAT` | Allow chat in signout channels | `false` |
| `AUTO_CREATE_TOOLS` | Auto-create tools from channels | `true` |
| `CLEANUP_INTERVAL_MINUTES` | Cleanup task interval | `1` |
| `TIMEZONE` | Bot timezone | `America/Chicago` |
| `LOG_LEVEL` | Logging level | `INFO` |

## Testing

### Unit Tests (To be implemented)
```python
# tests/test_time_utils.py
def test_parse_time_range():
    start, end = parse_time_range("01-15-2025 10:00 to 01-15-2025 12:00")
    assert start.hour == 10
    assert end.hour == 12

# tests/test_repositories.py
def test_reservation_conflict():
    # Mock session, test conflict detection
    pass
```

### Integration Tests
```python
# tests/test_integration.py
def test_full_reservation_flow():
    # Test create, check conflicts, return, archive
    pass
```

## Performance Considerations

### Database Optimization
- Composite indexes on frequently queried columns
- Connection pooling for PostgreSQL
- Prepared statements via SQLAlchemy
- Lazy loading of relationships

### Caching Strategy (Future)
- Cache active reservations in Redis
- Invalidate on updates
- TTL-based for statistics

## Rollback Plan

If issues arise:
1. Stop the bot
2. Restore from backups:
   ```bash
   mv tools.json.backup_TIMESTAMP tools.json
   mv history.csv.backup_TIMESTAMP history.csv
   ```
3. Revert to previous bot version
4. Investigate issues
5. Re-run migration with fixes

## Monitoring & Logging

### Log Levels
- **DEBUG**: Detailed diagnostic information
- **INFO**: General informational messages
- **WARNING**: Warning messages (non-critical)
- **ERROR**: Error messages (recoverable)
- **CRITICAL**: Critical errors (service impact)

### Key Metrics to Monitor
- Reservation creation rate
- Query performance
- Error rates
- Database connection pool status
- OpenAI API latency

## Future Enhancements

Once database migration is complete:
- [ ] Add Alembic for schema migrations
- [ ] Implement connection pooling
- [ ] Add database backups automation
- [ ] Create admin web dashboard
- [ ] Add GraphQL API
- [ ] Implement caching layer
- [ ] Add full-text search
- [ ] Real-time notifications via webhooks
- [ ] Export/import functionality
- [ ] Multi-guild support

## Support & Documentation

- **Configuration**: See `.env.example`
- **API Docs**: See `repositories.py` docstrings
- **Database Schema**: See `database.py`
- **Migration Guide**: This document

## Credits

Refactored by: GitHub Copilot  
Date: November 24, 2025  
Version: 2.0.0
