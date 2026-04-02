# Photo Enforcement System

## Overview

The photo enforcement system ensures Tool Room tools have proper photo documentation by requiring users to provide photos at signout and return. The system uses grace periods, automated warnings, and access restrictions to maintain compliance.

---

## How It Works

### For Tool Room Reservations

**At Signout:**
- If reservation starts > 30 minutes away: Photo is optional (can be provided later)
- If reservation starts ≤ 30 minutes away: Photo required immediately
- All Tool Room reservations are marked `photo_required=True`

**15 Minutes Before Start:**
- Pre-start reminder sent to user
- If photo not provided yet: Warning included in reminder
- User can send photo via DM

**At Reservation Start:**
- System checks if photo provided
- If missing: Warning sent with 10-minute grace period
- User can still send photo via DM

**10 Minutes After Start:**
- If still no photo: Reservation automatically cancelled
- Photo debt created (30-minute grace period)
- User notified and admin channel alerted

**At Return:**
- Photo preferred but not strictly required
- If user returns without photo: Photo debt created (30-minute grace period)
- User has 30 minutes to send photo via DM

**Photo Debt Enforcement:**
- After 30-minute grace period expires: User blocked from ALL Tool Room signouts
- Block remains until photo is provided or admin clears debt
- User notified of block status
- Admin channel receives enforcement notification

---

## User Experience

### Providing Photos

**Method 1: At Command Time**
```
/signout time:now for 2 hours photo:[attach image]
/returntool reservation:[select] photo:[attach image]
```

**Method 2: Via DM (Recommended for Future Reservations)**
1. Create reservation without photo (if starts > 30 min away)
2. Receive reminder 15 min before start
3. Send photo to bot via DM (direct message)
4. Bot automatically attaches photo to reservation

### Photo Debt Resolution

**If you have photo debt:**
1. Check DM from bot explaining what photo is needed
2. Send the required photo to bot via DM
3. Debt is automatically resolved
4. You can use Tool Room tools again

**Contacting Admin:**
- Use `/clearphotodebt` if you need manual assistance
- Admins can clear debts for legitimate reasons

---

## Admin Management

### Commands

**`/clearphotodebt user:<username>`**
- Clears all outstanding photo debts for a user
- Use for legitimate exceptions (emergencies, technical issues)
- Logs admin action for audit trail

**`/photoaudit`**
- View all outstanding photo debts across all users
- Shows up to 10 users with most recent debts
- Summary of total debts and affected users

**`/photodebts tool:<name>`**
- View photo debts for a specific tool
- Can be run in tool channel (auto-detects tool)
- Shows up to 15 debts with due dates

### Monitoring

**Admin Channel Notifications:**
- Reservation cancelled for missing start photo
- User blocked for overdue photo debt
- Includes user, tool, timestamp, and debt type

**Log Messages:**
- Photo warnings sent
- Photo debts created
- Photo debts resolved
- Admin debt clearances

---

## Technical Details

### Database Schema

**Reservations Table (New Fields):**
```sql
photo_required BOOLEAN DEFAULT FALSE  -- Is photo required?
photo_reminder_sent_at TIMESTAMP      -- When reminder was sent
photo_warning_sent_at TIMESTAMP       -- When warning was sent
```

**Photo Debts Table:**
```sql
CREATE TABLE photo_debts (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) REFERENCES users(user_id),
    tool_id INTEGER REFERENCES tools(id),
    reservation_id INTEGER,
    username VARCHAR(100),
    tool_name VARCHAR(100),
    debt_type ENUM('start', 'return'),
    photo_url TEXT,
    resolved_at TIMESTAMP,
    cleared_by_admin BOOLEAN,
    admin_user_id VARCHAR(50),
    created_at TIMESTAMP,
    due_at TIMESTAMP
)
```

**New Reservation Statuses:**
- `CANCELLED_NO_START_PHOTO` - Cancelled for missing start photo
- `CANCELLED_NO_RETURN_PHOTO` - (Reserved for future use)

### Grace Periods

| Event | Grace Period | Action if Expired |
|-------|-------------|-------------------|
| Reservation starts | 10 minutes | Cancel reservation, create photo debt |
| Photo debt created | 30 minutes | Block user from Tool Room signouts |

### DM Photo Handler Logic

When user sends image via DM:
1. Check for active photo debts (prioritize RETURN type)
2. If debt found: Attach photo, resolve debt, notify user
3. If no debt: Check for active reservations needing photos
4. Prioritize reservation starting soonest
5. Attach photo to reservation, confirm to user

### Background Tasks

**`check_photo_grace_periods()` - Runs every minute:**
- Finds reservations that started without photos
- Sends warnings at start time
- Cancels reservations 10 min after start
- Creates photo debts with 30-min grace period

**`check_photo_debt_enforcement()` - Runs every minute:**
- Finds overdue photo debts (past 30-min grace)
- Notifies users they're blocked
- Notifies admin channel
- Debts remain active until resolved

---

## Edge Cases

### User Cancels Reservation Before Start
- No photo debt created
- Proper cancellation via `/cancel` clears all requirements

### Admin Force-Returns Tool
- Uses same logic as user return
- Admin can choose to skip photo requirement
- Photo debt still created if photo not provided (admin can clear immediately)

### Reservation Expires Naturally
- If no photo provided: Photo debt created
- Admin notified
- 30-minute grace period applies

### Multiple Active Reservations
- DM photo attaches to soonest-starting reservation needing photo
- User can send multiple photos for multiple reservations

### User Sends Wrong Photo
- Any image accepted (no validation of content)
- Assumes user is providing good-faith documentation
- Admins can review photos in history if needed

---

## Deployment Steps

### 1. Run Migration

```bash
cd /opt/signout/signout-discord-bot
python3 helper_scripts/migrate_photo_enforcement.py
```

This adds:
- New columns to `reservations` table
- New `photo_debts` table
- New enum values for statuses
- Indexes for performance

### 2. Restart Bot

```bash
sudo systemctl restart test-signout
```

### 3. Verify Functionality

**Test Photo Upload via DM:**
1. Create Tool Room reservation starting soon
2. Don't provide photo
3. Wait for reminder
4. Send photo to bot via DM
5. Verify photo attached to reservation

**Test Photo Debt:**
1. Return tool without photo
2. Verify warning message
3. Send photo via DM within 30 min
4. Verify debt resolved

**Test Admin Commands:**
1. Run `/photoaudit` to see active debts
2. Create test debt
3. Run `/clearphotodebt` to clear it
4. Verify user can sign out again

---

## Best Practices

### For Users
- Provide photos at signout/return time when possible
- Set up DM notifications to receive warnings
- Send photos via DM if you forget during command
- Contact admin if you have technical issues

### For Admins
- Monitor admin channel for enforcement notifications
- Review `/photoaudit` weekly to see patterns
- Clear debts for legitimate exceptions (emergencies, etc.)
- Remind users about photo requirements in announcements

---

## Troubleshooting

**User can't sign out Tool Room tool:**
- Check `/photoaudit` for their debts
- Review when debt was created
- Determine if photo was actually needed
- Use `/clearphotodebt` if appropriate

**Photo not attaching via DM:**
- Verify user sent image file (PNG, JPG, etc.)
- Check bot logs for errors
- Verify user has active reservation or debt
- Try manual photo upload via command instead

**Reservation cancelled unexpectedly:**
- Check if it was Tool Room reservation
- Verify if photo was provided
- Check notification logs for warnings sent
- Review 10-minute grace period timing

**False photo debt:**
- Verify reservation was actually Tool Room
- Check if photo was provided at command time
- Review notification logs
- Clear debt and investigate bot logs

---

## Future Enhancements

Potential improvements:
- Photo quality validation (resolution, file size)
- OCR for tool tag verification
- Photo comparison (before/after)
- Automatic photo debt warnings before enforcement
- Statistics on photo compliance rates
- Per-tool photo requirement override
- Photo debt appeal system

