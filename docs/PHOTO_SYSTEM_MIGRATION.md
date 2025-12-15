# Photo Management System - Migration Guide

## Overview

The photo management system has been upgraded from a single `photo_url` column to a comprehensive multi-photo system with admin review capabilities. This addresses the requirement that Tool Room reservations need **multiple photos** (start photos within grace period, and return photos at the end).

## What Changed

### Database Schema

#### New Table: `reservation_photos`
```sql
- id (serial primary key)
- reservation_id (foreign key to reservations)
- photo_type (enum: 'start' or 'return')
- photo_url (text, not null)
- uploaded_at (timestamp)
- user_id, username, tool_name (cached for convenience)
- Admin review fields:
  - reviewed_by_user_id
  - reviewed_by_username  
  - reviewed_at
  - approved (boolean: true=approved, false=rejected, null=pending)
  - review_notes
```

#### Modified Tables
- **reservations**: Removed `photo_url` column (now uses relationship to `reservation_photos`)
- **reservation_history**: Renamed `photo_url` to `photo_urls` (stores JSON array of photos for historical records)

### Code Changes

#### 1. **database.py**
- Added `PhotoTypeEnum` ('start', 'return')
- Added `ReservationPhotoModel` table with admin review fields
- Updated `ReservationModel` to remove `photo_url` column and add `photos` relationship
- Updated `ReservationHistoryModel` to use `photo_urls` (JSON) instead of `photo_url`

#### 2. **models.py**
- Added `PhotoType` enum
- Added `ReservationPhoto` dataclass with review fields
- Updated `Reservation` dataclass:
  - Changed `photo_url` to `photos: List[ReservationPhoto]`
  - Added helper methods:
    - `get_start_photos()` - Get all start photos
    - `get_return_photos()` - Get all return photos
    - `has_start_photo()` - Check if has start photo
    - `has_return_photo()` - Check if has return photo
    - `get_approved_start_photos()` - Get approved start photos
    - `get_approved_return_photos()` - Get approved return photos

#### 3. **repositories.py**
- Updated imports to include `ReservationPhotoModel` and `PhotoTypeEnum`
- Removed `photo_url` parameter from `ReservationRepository.create()`
- Updated `archive_reservation()` to serialize photos to JSON for history
- Added `ReservationPhotoRepository` class with methods:
  - `add_photo()` - Add a photo to a reservation
  - `get_photos()` - Get all photos for a reservation
  - `get_photos_by_type()` - Get photos by type (start/return)
  - `get_pending_review_photos()` - Get photos pending admin review
  - `review_photo()` - Approve/reject a photo
  - `bulk_approve_photos()` - Approve all pending photos for a reservation
  - `get_photo_by_id()` - Get specific photo
  - `delete_photo()` - Delete a photo
  - `convert_to_model()` - Convert DB model to dataclass

#### 4. **mainbot.py**
- Updated DM photo handler to use `ReservationPhotoRepository.add_photo()` with `PhotoTypeEnum.START`
- Updated return tool command to use `ReservationPhotoRepository.add_photo()` with `PhotoTypeEnum.RETURN`

#### 5. **notifications.py**
- Updated reminder notification to check for start photos
- Updated expiration warning to use first available photo
- Updated photo enforcement check to use LEFT JOIN with `reservation_photos` table

#### 6. **admin_panel.py**
- Added `photo_group` command group under `/admin`
- New commands:
  - `/admin photo pending [limit]` - View photos pending review
  - `/admin photo approve <photo_id> [notes]` - Approve a photo
  - `/admin photo reject <photo_id> <notes>` - Reject a photo
  - `/admin photo view <photo_id>` - View photo details
  - `/admin photo bulkapprove <reservation_id>` - Bulk approve all pending photos

## Migration

### Prerequisites
1. **Backup your database** before running migration
2. Ensure all users are notified of the upcoming change
3. Schedule downtime if possible (migration is fast but safe)

### Running the Migration

```bash
# Test first (dry run - no changes committed)
python helper_scripts/migrate_photo_table.py --dry-run

# Run actual migration
python helper_scripts/migrate_photo_table.py
```

### What the Migration Does

1. Creates `PhotoTypeEnum` type
2. Creates `reservation_photos` table with indexes
3. Migrates existing `photo_url` data from `reservations` table
   - All existing photos are classified as "start" photos (since type is unknown)
   - Photos are marked as pending review (admin can bulk approve if needed)
4. Renames `photo_url` to `photo_urls` in `reservation_history`
5. Drops `photo_url` column from `reservations`
6. Verifies migration success

### Post-Migration Steps

1. Restart the Discord bot
2. Test photo upload:
   - Create a Tool Room reservation
   - Upload a start photo via DM
   - Return the tool with a return photo
3. Test admin review:
   - Run `/admin photo pending` to see pending photos
   - Approve or reject photos using `/admin photo approve` or `/admin photo reject`
4. If you have many legacy photos to approve, use `/admin photo bulkapprove <reservation_id>`

## Usage

### For Users

**Uploading Start Photos:**
1. Sign out a Tool Room tool
2. Within the grace period (usually 30 min), send a photo of the tool to the bot via DM
3. Bot will attach it to your earliest reservation needing a start photo

**Uploading Return Photos:**
1. When returning a Tool Room tool, use `/returntool` and attach the return photo
2. The photo will be added to the reservation

### For Admins

**Reviewing Photos:**
```
/admin photo pending          - See all pending photos
/admin photo view 123         - View details of photo ID 123
/admin photo approve 123      - Approve photo ID 123
/admin photo reject 123 "Blurry image, please resubmit"
/admin photo bulkapprove 456  - Approve all photos for reservation ID 456
```

**Photo Review Status:**
- ⏳ **Pending** (approved = null) - Awaiting admin review
- ✅ **Approved** (approved = true) - Photo accepted
- ❌ **Rejected** (approved = false) - Photo rejected with reason

## Benefits of New System

1. **Multiple Photos Per Stage**: Users can upload multiple start photos and multiple return photos
2. **Photo Type Tracking**: System knows which photos are for start vs return
3. **Admin Review**: Admins can approve/reject photos with notes
4. **Audit Trail**: Track who reviewed photos and when
5. **Historical Records**: Photo data preserved in JSON format in history table
6. **Better Enforcement**: Can check specifically for start or return photos
7. **Scalability**: Can add more photo types in future if needed

## Rollback (Emergency Only)

If you need to rollback:

```sql
-- 1. Re-add photo_url column to reservations
ALTER TABLE reservations ADD COLUMN photo_url TEXT;

-- 2. Copy first photo back (if needed)
UPDATE reservations r
SET photo_url = (
    SELECT photo_url FROM reservation_photos
    WHERE reservation_id = r.id
    ORDER BY uploaded_at
    LIMIT 1
);

-- 3. Rename history column back
ALTER TABLE reservation_history RENAME COLUMN photo_urls TO photo_url;

-- 4. Optionally drop new table
-- DROP TABLE reservation_photos;
```

**Note**: This will lose multi-photo data and review status. Only use in emergencies.

## Future Enhancements

Potential additions to the photo system:

1. **Auto-approval for trusted users**: Skip review for users with good history
2. **Photo quality checks**: Use AI to verify photo shows the actual tool
3. **Notification to users**: Alert users when photo is approved/rejected
4. **Photo comparison**: Compare start and return photos to verify same tool
5. **Photo expiration**: Auto-delete photos after certain time period
6. **User photo history**: Let users view their photo upload history

## Troubleshooting

**"Photo ID not found"**
- Photo may have been deleted or ID is incorrect
- Use `/admin photo pending` to see available photos

**"No pending photos"**
- All photos have been reviewed
- Check `/admin photo view <id>` to see review status

**Migration fails with "column already exists"**
- Migration was partially run before
- Check database schema manually and adjust migration script

**Photos not showing in notifications**
- Check that reservation has photos: `SELECT * FROM reservation_photos WHERE reservation_id = X`
- Verify relationship is loaded correctly in code

## Support

For issues or questions:
1. Check logs: `/debug logs tail`
2. Review database: `SELECT * FROM reservation_photos WHERE approved IS NULL;`
3. Contact developer with error details

---

**Last Updated**: December 15, 2025
**Migration Script**: `helper_scripts/migrate_photo_table.py`
**Documentation**: This file
