# Admin Quick Reference Guide

This guide covers all administrative commands and workflows for managing the Tool Signout Bot.

---

## Overview

As an admin, you have access to commands for:
- Managing tools and configurations
- Handling reservations and conflicts
- Creating maintenance blocks
- Setting consecutive signout limits
- Managing user roles and permissions
- Reviewing and managing photos
- Clearing photo debts
- Monitoring system health

**Admin privileges:** All admin commands require the Administrator permission in Discord.

---

## Quick Command Reference

### Most Common Admin Tasks

| Task | Command |
| :---- | :---- |
| Add a new tool | `/admin tool add tool:<name>` |
| Block a tool for maintenance | `/admin block add tool:<select> time:<range>` |
| Sign out tool for user | `/admin signout user:<name> tool:<name> time:<range>` |
| Clear all reservations | `/admin reservation clear` |
| Force return a reservation | `/admin reservation forcereturn` |
| Assign tool access to user | `/admin role assign user:<name>` |
| View photos for approval | `/admin photo view tool:<name> username:<user> timerange:<range>` |
| Approve a photo | `/admin photo approve photo_id:<id>` |
| Clear a photo debt | `/clearphotodebt user:<name>` |
| View outstanding photo debts | `/photoaudit` |
| Set consecutive signout limits | `/admin limit set max:<int> cooldown:<hours>` |

---

## Tool Management

### Adding a New Tool

In any channel:
```
/admin tool add tool:<tool_name> max_hours:<number>
```

**Parameters:**
- `tool`: Tool name (required)
- `max_hours`: Maximum reservation duration in hours (default: 168)

**What happens automatically:**
1. Tool is created in the database
2. Discord role is created (e.g., @bandsaw)
3. Role requirement is enabled by default
4. If channel is in "Tool Room" category, photo enforcement is enabled

**Example:**
```
/admin tool add tool:bandsaw max_hours:4
```

### Removing a Tool

```
/admin tool remove tool:<tool_name>
```

This will:
- Archive all existing reservations to history
- Delete the tool from active tools
- Keep all historical data

### Adjusting Maximum Time

In the tool's signout channel:
```
/admin tool maxtime hours:<number>
```

Changes the maximum reservation duration for that tool.

### Managing Role Requirements

In the tool's signout channel:

**Toggle role requirement on/off:**
```
/admin role toggle
```

**Assign tool access to a user:**
```
/admin role assign user:<username>
```

**Remove user access:**
```
/admin role revoke user:<username>
```

**Sync all tool roles:**
```
/admin role sync
```
Creates missing roles for all tools in the database.

---

## Admin Blocks (Maintenance Windows)

Admin blocks prevent users from creating reservations during maintenance or special events.

### Creating a Block

```
/admin block add tool:<select> time:<range> force:<true|false>
```

**Parameters:**
- `tool`: Search and select one or more tools from autocomplete; type a comma between selections. Choose `[All Tools]` alone to select every tool.
- `time`: Natural language time range (e.g., "tomorrow 9am to 5pm", "friday all day")
- `force`: For named tools only, modify conflicting future reservations. It never overrides a reservation currently in use and is ignored for `[All Tools]`.

**Examples:**
```
/admin block add tool:laser-cutter time:friday 8am to 12pm force:false
/admin block add tool:laser-cutter, drill-press time:friday 8am to 12pm force:false
/admin block add tool:[All Tools] time:12/25 all day force:true
```

**Behavior:**
- Blocks are treated as ADMIN_BLOCK reservations by user "admin-block"
- `[All Tools]` skips every tool with a reservation overlapping the requested time, even if `force=true`
- Named-tool blocks with `force=true` may shorten or cancel conflicting future reservations, but never a reservation currently in use
- Automatically removed after end time passes

### Viewing Active Blocks

```
/admin block list
```

Shows all active maintenance blocks across all tools.

### Removing a Block

```
/admin block remove block:<select from dropdown>
```

Select the block from the autocomplete list and it will be immediately removed.

---

## Managing Reservations

### Sign Out Tool on Behalf of User

```
/admin signout user:<username> tool:<tool_name> time:<range> photo:<optional>
```

**Parameters:**
- `user`: Username to create reservation for (autocomplete)
- `tool`: Tool to reserve (autocomplete dropdown)
- `time`: Natural language time range (e.g., "now for 2 hours", "tomorrow 3pm-5pm")
- `photo`: Optional photo attachment (required for Tool Room if starting soon)

**What happens:**
- Creates reservation as if the user had signed out themselves
- Validates user exists in database (they must have used bot at least once)
- Checks for conflicts with existing reservations
- Respects maximum time limits
- Applies Tool Room photo requirements if applicable
- Does NOT bypass role requirements or consecutive limits

**Example:**
```
/admin signout user:john tool:laser-cutter time:tomorrow 2pm for 3 hours
```

**Use cases:**
- Signing out tools for users without Discord access
- Creating reservations for events or classes
- Helping users who are having technical difficulties
- Reserving tools for scheduled maintenance by specific staff

**Notes:**
- User must exist in database (have used the bot before)
- Tool must exist (create with `/admin tool add` if needed)
- Admin still needs appropriate tool role if role_required is enabled
- For Tool Room tools, photo may be required depending on start time

### Clear All Reservations

In the tool's signout channel:
```
/admin reservation clear
```

Cancels all active reservations for the current tool.

### Force Return a Reservation

In the tool's signout channel:
```
/admin reservation forcereturn
```

Immediately returns the first active reservation for the tool without requiring user action.

### Adjust Another User's Reservation

```
/adjusttime_admin user:<username> old_time:<time> choice:<start|end|range> new_value:<value>
```

Allows you to modify any user's reservation time.

---

## Photo Management

### Disable Photo Enforcement

Turn photo enforcement off without deleting existing photo records:
```
/admin photo bypass enabled:false
```

Disable enforcement, resolve pending photo debts (restoring signout access), and delete only photos still awaiting review. Reviewed photos, resolved debt records, and reservation-history photo URLs are retained:
```
/admin photo bypass enabled:false clear_data:true
```

Re-enable enforcement for new reservations:
```
/admin photo bypass enabled:true
```

Disabling photo enforcement clears outstanding `photo_required` flags so existing reservations do not continue sending photo reminders. The clear option does not delete reviewed photos or reservation history.

### Understanding Photo Requirements

**Tool Room Detection:**
- Tools in channels under "Tool Room" category require photos
- Start photo: Required when signing out
- Return photo: Required when returning or when reservation expires

**Photo Debts:**
- START debts: User failed to provide start photo (blocks immediately)
- RETURN debts: User failed to provide return photo (30-minute grace period)

**Grace Periods:**
- Start photo: 10 minutes after reservation start time
- Return photo: 30 minutes after reservation ends
- After grace period: User is blocked from Tool Room until admin clears debt

### View Photos

```
/admin photo view tool:<tool_name> username:<username> timerange:<natural_language>
```

Query photos using natural language time ranges. Photos are sent to you via DM.

**Examples:**
```
/admin photo view tool:tracksaw username:mpm2122 timerange:today
/admin photo view tool:welder1 username:john timerange:last week
/admin photo view tool:domino username:jane timerange:december 1 to december 15
/admin photo view tool:laser-cutter username:user timerange:yesterday
```

**What you'll receive:**
- DM containing all matching photos
- Each photo shows: tool name, username, type (start/return), timestamp
- Photo ID for approval/rejection
- Embedded image for review

### Approve a Photo

After viewing photos via DM:
```
/admin photo approve photo_id:<id> notes:<optional_notes>
```

**Parameters:**
- `photo_id`: The ID shown in the photo DM
- `notes`: Optional admin notes about the approval

Approving a photo does not clear debts automatically. Use `/clearphotodebt` to unblock users.

### Reject a Photo

```
/admin photo reject photo_id:<id> notes:<reason>
```

**Parameters:**
- `photo_id`: The ID shown in the photo DM
- `notes`: Reason for rejection (recommended)

Rejected photos remain in the system for audit purposes.

### Bulk Approve Photos

```
/admin photo bulkapprove reservation_id:<id>
```

Approves all pending photos for a specific reservation. Use when you trust the user's photos without individual review.

### View All Outstanding Photo Debts

```
/photoaudit
```

Shows up to 10 users with outstanding photo debts:
- Username and tool name
- Debt type (START or RETURN)
- When the debt is due
- Whether grace period is active

### View Debts for Specific Tool

```
/photodebts tool:<tool_name>
```

Shows all photo debts for a specific tool.

### Clear a User's Photo Debt

```
/clearphotodebt user:<username>
```

**When to use:**
- After verifying the missing photo via DM or in person
- After confirming tool condition directly
- To resolve disputes or technical issues

**Important:** Only admins can clear photo debts. Users cannot clear their own debts to prevent abuse.

---

## Consecutive Signout Limits

Prevent users from monopolizing high-demand tools by limiting consecutive reservations.

### How It Works

- User signs out a tool multiple times in a row
- After reaching the limit, they enter a cooldown period
- Counter resets when someone else uses the tool
- Counter also resets after a period of inactivity (24 hours default with minimum total hours)

### Setting Limits

In the tool's signout channel:
```
/admin limit set max_consecutive:<number> cooldown_hours:<hours> reset_after_hours:<hours> min_total_hours:<hours>
```

**Parameters:**
- `max_consecutive`: Maximum consecutive signouts before cooldown (0 = no limit)
- `cooldown_hours`: Hours user must wait after reaching limit
- `reset_after_hours`: Auto-reset after this many hours of inactivity (default: 24)
- `min_total_hours`: Only reset if total accumulated time is below this (default: 48)

**Example:**
```
/admin limit set max_consecutive:3 cooldown_hours:24 reset_after_hours:24 min_total_hours:48
```

Users can sign out the tool 3 times in a row, then must wait 24 hours. If they don't sign out for 24 hours AND have less than 48 total hours, their counter resets.

### Viewing Current Limits

```
/admin limit view
```

Shows all tools with configured limits and their settings.

### Checking Who's in Cooldown

In the tool's signout channel:
```
/admin limit check
```

Lists:
- Users currently in cooldown (with time remaining)
- Users approaching the limit (with their consecutive count)

### Clearing a User's Cooldown

In the tool's signout channel:
```
/admin limit clear username:<username>
```

Immediately resets their consecutive count and removes cooldown for the current tool.

---

## User Exemptions

Grant specific users exemption from consecutive signout limits.

### Add Exemption

In the tool's signout channel:
```
/admin exempt add username:<username> duration_hours:<hours> reason:<text>
```

**Parameters:**
- `username`: User to exempt
- `duration_hours`: Hours exemption lasts (omit for permanent)
- `reason`: Optional reason for audit trail

**Example:**
```
/admin exempt add username:instructor_john duration_hours:720 reason:Teaching course this month
```

### Remove Exemption

In the tool's signout channel:
```
/admin exempt remove username:<username>
```

### View All Exemptions

In the tool's signout channel:
```
/admin exempt list
```

Shows all users with exemptions for the current tool, including:
- Username
- Duration (permanent or expiration date)
- Who granted it
- Reason

---

## Monitoring and Debugging

### Test Notifications

```
/testnotify
```

Sends a test notification to verify the notification system is working properly.

### Daily Summary

```
/adminsummary
```

Sends a summary of all active reservations to all admins via DM.

### View User Statistics

```
/mystats user:<username>
```

Shows detailed statistics for a user:
- Total reservations
- Most used tools
- Average reservation duration
- Recent activity

### Debug Logs

**View recent logs:**
```
/debug logs tail lines:<number>
```

**Set log level:**
```
/debug logs level level:<CRITICAL|ERROR|WARNING|INFO|DEBUG>
```

**Stream logs to channel:**
```
/debug logs watch enable:<true|false>
```

Enable to stream logs in real-time to the current channel. Disable to stop streaming.

---

## Common Admin Workflows

### Setting Up a New Tool

1. Add the tool:
   ```
   /admin tool add tool:new-tool max_hours:4
   ```

2. Verify role was created:
   - Check server roles for @new-tool

3. Assign initial trained users:
   ```
   /admin role assign user:username1
   /admin role assign user:username2
   ```

4. Set consecutive limits if high-demand:
   ```
   /admin limit set max_consecutive:3 cooldown_hours:24
   ```

5. If Tool Room tool, verify photo enforcement is active:
   - Sign out the tool
   - Confirm photo is required

### Scheduling Maintenance

1. Create an admin block:
   ```
   /admin block add tool:laser-cutter time:tomorrow 9am to 11am force:true
   ```

2. System automatically:
   - Cancels conflicting reservations (if force=true)
   - Prevents new reservations during maintenance
   - Notifies affected users

3. After maintenance, remove block:
   ```
   /admin block remove block:<select>
   ```

### Managing Photo Debt Issues

1. Check outstanding debts:
   ```
   /photoaudit
   ```

2. View user's photos:
   ```
   /admin photo view tool:welder1 username:john timerange:last week
   ```

3. Review photos in your DM

4. If photos are acceptable, approve them:
   ```
   /admin photo approve photo_id:123 notes:Photo shows proper storage
   ```

5. Clear the debt to unblock user:
   ```
   /clearphotodebt user:john
   ```

### Handling Unresponsive Users

1. Check if user has active reservation:
   ```
   /mystats user:username
   ```

2. Attempt to contact user (DM or mention)

3. If no response, force return:
   ```
   /admin reservation forcereturn
   ```

4. Tool is now available for others

### Tool Room Channel Setup

1. Create a Discord channel category named "Tool Room" (exact case)

2. Move or create signout channels under that category

3. System automatically detects and enables:
   - Photo requirements for signouts
   - Photo requirements for returns
   - Grace period enforcement
   - Photo debt system

**No additional configuration required.**

---

## Photo System Reference

### Photo Requirements by Situation

**Immediate Reservations (starting now or within 30 minutes):**
- Photo required immediately when signing out
- 10-minute grace period after start time
- Reservation cancelled if no photo after grace period
- START debt created (blocks user immediately)

**Future Reservations (starting later than 30 minutes):**
- Photo can be sent via DM anytime before start
- Must be sent within 10 minutes after start time
- Reservation cancelled if no photo after grace period
- START debt created (blocks user immediately)

**Return Photos:**
- Should be provided with `/returntool` command
- Can be sent via DM if forgotten
- 30-minute grace period after reservation ends
- User can clear RETURN debt themselves within grace period
- After grace period: RETURN debt requires admin clearance

### Photo Debt Consequences

**START Debts (missing start photo):**
- User blocked from ALL Tool Room tools immediately
- Cannot be self-cleared
- Requires admin to clear: `/clearphotodebt`

**RETURN Debts (missing return photo):**
- Within 30 minutes: User can upload photo via DM to self-clear
- After 30 minutes: User blocked from ALL Tool Room tools
- Requires admin to clear: `/clearphotodebt`

### Photo Review Process

1. User uploads photo (via command or DM)
2. Photo stored in database with metadata
3. Admin reviews using `/admin photo view`
4. Admin approves or rejects: `/admin photo approve` or `/admin photo reject`
5. Photos persist permanently in database for audit trail
6. Approved/rejected status tracked for compliance

---

## Tips for Effective Administration

**Tool Management:**
- Enable role requirements for safety-critical tools (saws, welders, mills)
- Set realistic max_hours based on typical project needs
- Use admin blocks for scheduled maintenance rather than manual clearing
- Sync roles periodically if Discord roles are modified externally

**Photo Enforcement:**
- Review photo debts regularly using `/photoaudit`
- Clear legitimate debts promptly to avoid user frustration
- Use photo queries to audit compliance over time
- Educate users on grace periods and photo requirements

**Access Control:**
- Set consecutive limits on high-demand tools (laser cutters, 3D printers)
- Exempt instructors and staff from limits as needed
- Review exemption list periodically to remove outdated entries
- Document exemption reasons for audit trail

**Monitoring:**
- Run `/adminsummary` daily to stay aware of reservations
- Check `/photoaudit` before leaving for the day
- Use `/mystats` to investigate user behavior issues
- Stream logs during troubleshooting: `/debug logs watch`

**User Support:**
- Respond to photo debt issues quickly
- Use force return sparingly (contact user first)
- Document dispute resolutions
- Direct users to USER_QUICKSTART.md for self-service help

---

## Troubleshooting

### Bot Not Responding to Commands

1. Check bot online status in Discord
2. Verify bot has proper permissions in channel
3. Check `/debug logs tail` for errors
4. Contact technical administrator if persistent

### Photos Not Being Required

1. Verify channel is under "Tool Room" category (exact case)
2. Check tool configuration: `/admin tool maxtime` (confirms tool exists)
3. Verify tool has `is_tool_room` flag set in database
4. Contact technical administrator if issue persists

### User Can't Clear Photo Debt

1. Check if debt is START or RETURN:
   ```
   /photoaudit
   ```

2. START debts: Only admin can clear (by design)

3. RETURN debts:
   - Within 30 minutes: User uploads photo via DM to self-clear
   - After 30 minutes: Only admin can clear

4. Verify photo in system:
   ```
   /admin photo view tool:<name> username:<user> timerange:today
   ```

5. If photo exists and is acceptable:
   ```
   /clearphotodebt user:<username>
   ```

### Consecutive Limit Not Working

1. Verify limit is set:
   ```
   /admin limit view
   ```

2. Check if user is exempted:
   ```
   /admin exempt list
   ```

3. Verify other users have used tool between reservations (resets counter)

4. Check user stats:
   ```
   /mystats user:<username>
   ```

### Database or Critical Errors

1. Check logs immediately:
   ```
   /debug logs tail lines:100
   ```

2. Do not attempt manual database operations

3. Contact technical administrator with:
   - Error messages from logs
   - What command was run
   - When issue started

4. Document what happened for post-mortem

---

## Command Reference Summary

### Tool Management
- `/admin tool add` - Create new tool
- `/admin tool remove` - Delete tool
- `/admin tool maxtime` - Set max reservation duration
- `/admin role toggle` - Enable/disable role requirement
- `/admin role assign` - Give user tool access
- `/admin role revoke` - Remove user tool access
- `/admin role sync` - Create missing tool roles

### Reservations
- `/admin signout` - Create reservation on behalf of user
- `/admin reservation clear` - Cancel all reservations
- `/admin reservation forcereturn` - Force return active reservation
- `/adjusttime_admin` - Modify user's reservation time

### Admin Blocks
- `/admin block add` - Create maintenance block
- `/admin block remove` - Remove maintenance block
- `/admin block list` - View all active blocks

### Photo Management
- `/admin photo bypass` - Disable/re-enable photo enforcement; optionally clear debts and stored photo data
- `/admin photo view` - Query and view photos
- `/admin photo approve` - Approve a photo
- `/admin photo reject` - Reject a photo
- `/admin photo bulkapprove` - Approve all photos for reservation
- `/photoaudit` - View all outstanding photo debts
- `/photodebts` - View debts for specific tool
- `/clearphotodebt` - Clear user's photo debt

### Consecutive Limits
- `/admin limit set` - Configure consecutive signout limits
- `/admin limit view` - View all configured limits
- `/admin limit check` - See who's in cooldown
- `/admin limit clear` - Reset user's cooldown

### Exemptions
- `/admin exempt add` - Grant exemption from limits
- `/admin exempt remove` - Remove exemption
- `/admin exempt list` - View all exemptions

### Monitoring
- `/testnotify` - Test notification system
- `/adminsummary` - Daily reservation summary
- `/mystats` - View user statistics
- `/debug logs tail` - View recent logs
- `/debug logs level` - Set log verbosity
- `/debug logs watch` - Stream logs to channel

---

## Getting Help

**For Users:**
- Direct users to `USER_QUICKSTART.md`
- Basic commands: `/help` in Discord

**For Admins:**
- This guide covers most admin operations
- Technical documentation: `README.md` in `/docs`
- Backend configuration: Contact technical administrator

**For Technical Issues:**
- Check `/debug logs tail` first
- Review error messages carefully
- Contact technical administrator with specific error details
- Provide context: what command, what time, what happened

**For Feature Requests:**
- Document the use case clearly
- Discuss with technical team
- Consider workarounds with existing commands

