# Photo Persistence Fix

## Issue
Photos were being deleted from the `reservation_photos` table when reservations were archived/deleted from the `reservations` table. This meant:
- Photos couldn't be queried after reservations ended
- Historical photo data was lost
- `/admin photo view` commands couldn't find photos from past reservations

## Root Cause
The foreign key constraint on `reservation_photos.reservation_id` had the default CASCADE delete behavior. When a reservation was deleted from the `reservations` table during archiving, PostgreSQL automatically deleted all associated photos from `reservation_photos`.

## Solution
Modified the database schema to prevent cascade deletion:

1. **Changed `reservation_id` column to nullable**
   - Photos can now exist even without an active reservation reference
   
2. **Updated foreign key constraint to `ON DELETE SET NULL`**
   - When a reservation is deleted, the `reservation_id` field is set to NULL instead of deleting the photo
   - Photos persist perpetually in the database

3. **Photos now include cached metadata**
   - Photos already store: `user_id`, `username`, `tool_name`, `uploaded_at`, `photo_type`
   - These fields allow querying photos even after the parent reservation is deleted
   - No data loss occurs when `reservation_id` becomes NULL

## Implementation

### Database Changes
**File:** `database.py`
```python
# Before:
reservation_id = Column(Integer, ForeignKey("reservations.id"), nullable=False, index=True)

# After:
reservation_id = Column(Integer, ForeignKey("reservations.id", ondelete="SET NULL"), nullable=True, index=True)
```

### Migration Script
**File:** `helper_scripts/fix_photo_cascade_delete.py`
- Drops old foreign key constraint
- Makes `reservation_id` nullable
- Creates new constraint with `ON DELETE SET NULL`
- Verifies all photos were preserved

## Impact

### Before Fix
- Photos deleted when reservations archived → ❌ Lost forever
- Can't query historical photos → ❌ No photo audit trail
- `/admin photo view` only works for active reservations → ❌ Limited

### After Fix
- Photos persist perpetually → ✅ Complete historical record
- Can query photos by user/tool/time → ✅ Full audit capability
- `/admin photo view` works for all time ranges → ✅ Unrestricted queries
- `reservation_id` becomes NULL after archiving → ✅ Orphaned photos queryable via cached fields

## Photo Querying After Fix

Photos can now be queried using cached metadata fields:
- **By user:** `username` field
- **By tool:** `tool_name` field  
- **By time:** `uploaded_at` field
- **By type:** `photo_type` (START or RETURN)
- **By approval status:** `approved`, `reviewed_at`, `reviewed_by_username`

Example queries in `/admin photo view`:
```
/admin photo view tool:tracksaw username:mpm2122 timerange:"last week"
/admin photo view tool:welder1 username:john timerange:"December 1 to December 10"
/admin photo view tool:domino username:jane timerange:"today"
```

## Verification

Run the verification query to check for photos with NULL `reservation_id`:
```sql
SELECT COUNT(*) FROM reservation_photos WHERE reservation_id IS NULL;
```

These are photos whose parent reservations have been archived - this is expected and normal after this fix.

## Deployment

**Migration applied:** December 15, 2025

**Steps taken:**
1. Updated `database.py` schema
2. Ran `helper_scripts/fix_photo_cascade_delete.py`
3. Restarted bot service
4. Verified foreign key constraint: `ON DELETE SET NULL`

**No data loss:** All existing photos were preserved during migration (0 photos in test database at time of migration).

## Future Considerations

- Photos now accumulate indefinitely in the database
- Consider implementing a photo archival/cleanup policy if storage becomes a concern (e.g., archive photos older than 1 year)
- Photos remain fully queryable regardless of reservation status
- The `reservation_id` field being NULL is a feature, not a bug - it indicates an archived reservation
