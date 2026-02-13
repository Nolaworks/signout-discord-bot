# Quick Reference: Photo System Changes

## What You Asked For

> "the database table reservations only has one column for photo_url. considering a tool room reservation requires a MINIMUM of two photos (one or more photos within the grace period at the start. and one or more at the end of the reservation). doesn't it make sense to have multiple columns for start_photo_urls and return_photo_urls?"

## Solution Implemented: Option 3 (Separate Photos Table)

### Why Option 3?
- ✅ Supports unlimited photos per reservation
- ✅ Distinguishes between start and return photos
- ✅ Includes admin review functionality (production requirement)
- ✅ Most flexible for future enhancements
- ✅ Proper database normalization

### Database Structure

```
reservation_photos (NEW TABLE)
├── id
├── reservation_id (FK → reservations.id)
├── photo_type (enum: 'start' | 'return')
├── photo_url
├── uploaded_at
├── user_id, username, tool_name (cached)
└── Admin Review Fields:
    ├── reviewed_by_user_id
    ├── reviewed_by_username
    ├── reviewed_at
    ├── approved (NULL=pending, TRUE=approved, FALSE=rejected)
    └── review_notes
```

### Key Changes Summary

| File | Change |
|------|--------|
| `database.py` | Added `ReservationPhotoModel` table, removed `photo_url` column |
| `models.py` | Added `ReservationPhoto` dataclass with helper methods |
| `repositories.py` | Added `ReservationPhotoRepository` with full CRUD |
| `mainbot.py` | Updated to use photo repository for uploads |
| `notifications.py` | Updated to query photos by type |
| `admin_panel.py` | Added `/admin photo` commands for review |
| `migrate_photo_table.py` | Migration script (with dry-run) |

### How It Works Now

**For Users:**
1. **Start Photos**: Send via DM within grace period
2. **Return Photos**: Attach when running `/returntool`
3. **Multiple Photos**: Can send multiple per stage

**For Admins:**
```bash
/admin photo pending          # See unreviewed photos
/admin photo approve <id>     # Approve a photo
/admin photo reject <id>      # Reject a photo (with reason)
/admin photo view <id>        # View details
/admin photo bulkapprove <id> # Approve all for reservation
```

### Migration Process

```bash
# 1. Backup database (CRITICAL!)
pg_dump your_database > backup.sql

# 2. Test migration (no changes)
python helper_scripts/migrate_photo_table.py --dry-run

# 3. Run migration
python helper_scripts/migrate_photo_table.py

# 4. Restart bot
systemctl restart signout-bot
```

### Code Example: Accessing Photos

```python
# Old way (REMOVED):
reservation.photo_url  # Single photo

# New way:
reservation.get_start_photos()     # List[ReservationPhoto]
reservation.get_return_photos()    # List[ReservationPhoto]
reservation.has_start_photo()      # bool
reservation.get_approved_start_photos()  # List[ReservationPhoto]

# In repositories:
from repositories import ReservationPhotoRepository
from database import PhotoTypeEnum

photo_repo = ReservationPhotoRepository(session)
photo_repo.add_photo(
    reservation_id=123,
    photo_type=PhotoTypeEnum.START,
    photo_url="https://...",
    user_id="12345",
    username="john_doe",
    tool_name="Laser Cutter"
)
```

### Files to Review Before Migration

1. ✅ `helper_scripts/migrate_photo_table.py` - Migration script
2. ✅ `docs/PHOTO_SYSTEM_MIGRATION.md` - Full documentation
3. ✅ `docs/PHOTO_IMPLEMENTATION_SUMMARY.md` - Change summary
4. ✅ `database.py` lines 30-35, 160-195 - New enum and table
5. ✅ `repositories.py` lines 927-1060 - New repository class
6. ✅ `admin_panel.py` lines 2000+ - New admin commands

### Next Action Items

- [ ] Review migration script
- [ ] Test on development database
- [ ] Run `--dry-run` migration on production
- [ ] Schedule maintenance window
- [ ] Execute production migration
- [ ] Restart bot
- [ ] Test photo upload and review workflow
- [ ] Train admins on new commands

---

**Status**: ✅ Implementation Complete
**Ready for**: Testing and Migration
**Estimated Migration Time**: < 5 minutes
**Downtime Required**: Bot restart only (< 1 minute)
