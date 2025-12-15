# Admin Quick Reference Guide

This guide covers all administrative commands and workflows for managing the Tool Signout Bot.

---

## Overview

As an admin, you have access to additional commands for:
- Managing tools and their settings
- Handling reservations and conflicts
- Creating admin blocks for maintenance
- Setting up consecutive signout limits
- Managing user roles and permissions
- Clearing photo debts
- Monitoring system health

**Admin privileges:** All admin commands require the Administrator permission in Discord.

---

## Quick Command Reference

Type `/adminhelp` in Discord for the full command list organized by category.

### Most Common Admin Tasks

| Task | Command |
| :---- | :---- |
| Add a new tool | `/admin tool add name:<tool>` |
| Block a tool for maintenance | `/admin block add tool:<select> time:<range>` |
| Clear all reservations for a tool | `/admin reservation clear` |
| Force return someone's reservation | `/admin reservation forcereturn` |
| Give user access to a tool | `/admin role assign user:<name>` |
| Clear a photo debt | `/clearphotodebt user:<name>` |
| View all photo debts | `/photoaudit` |
| Set consecutive signout limits | `/admin limit set max:<int> cooldown:<hours>` |

---

## Tool Management

### Adding a New Tool

```
/admin tool add name:<tool_name> max_hours:<number> role_required:<true|false>
```

**Default settings:**
- `max_hours`: Uses default from config if not specified
- `role_required`: `true` by default (recommended for safety)
- Tool Room detection: Automatically enabled if channel is in "Tool Room" category

**Example:**
```
/admin tool add name:bandsaw max_hours:4 role_required:true
```

**After adding a tool:**
1. The bot creates a dedicated role for the tool (e.g., "@bandsaw")
2. Tool Room channels automatically require photos
3. Users need the role to sign out the tool (unless you disable role requirement)

### Removing a Tool

```
/admin tool remove name:<tool_name>
```

**Warning:** This removes all reservations and history for that tool.

### Adjusting Max Time

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

**Give a user access:**
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
(Useful after Discord role changes or bot updates)

---

## Admin Blocks (Maintenance Windows)

Admin blocks prevent users from making reservations during specific times.

### Creating a Block

```
/admin block add tool:<select> time:<range> force:<true|false>
```

**Options:**
- `tool`: Select from dropdown. Choose `[All Tools]` to block everything.
- `time`: Natural language time range (e.g., "tomorrow 9am to 5pm")
- `force`: If `true`, cancels existing reservations in that time range

**Examples:**
```
/admin block add tool:laser-cutter time:friday 8am to 12pm force:false
/admin block add tool:[All Tools] time:12/25 all day force:true
```

### Viewing Active Blocks

```
/admin block list
```

Shows all active blocks across all tools.

### Removing a Block

```
/admin block remove block:<select from dropdown>
```

---

## Managing Reservations

### Clear All Reservations

In the tool's signout channel:
```
/admin reservation clear
```

**Warning:** This cancels ALL active reservations for the current tool.

### Force Return a Reservation

In the tool's signout channel:
```
/admin reservation forcereturn
```

Returns the currently active reservation (if any) without requiring the user to do it.

### Adjust Another User's Reservation

```
/admin reservation adjust
```

Allows you to modify any user's reservation time (similar to `/adjusttime` but for any user).

---

## Photo Management

### View All Outstanding Photo Debts

```
/photoaudit
```

Shows up to 10 users with outstanding photo debts, including:
- Username and tool name
- Whether it's a start or return photo
- When the debt is due

### View Debts for a Specific Tool

```
/photodebts tool:<tool_name>
```

### Clear a User's Photo Debt

```
/clearphotodebt user:<username>
```

**When to use:**
- User provides the missing photo to you directly
- You verify the tool condition in person
- Resolving a dispute or technical issue

**Important:** Users cannot clear their own photo debts. Only admins can clear them.

---

## Consecutive Signout Limits

Prevents users from monopolizing high-demand tools.

### Setting Limits

In the tool's signout channel:
```
/admin limit set max:<number> cooldown:<hours>
```

**Example:**
```
/admin limit set max:3 cooldown:24
```
Users can sign out the tool 3 times in a row, then must wait 24 hours.

**How it works:**
- Counter increases each time a user signs out the same tool consecutively
- Counter resets if someone else uses the tool between their reservations
- Admins are exempt from these limits

### Viewing Current Limits

```
/admin limit view
```

Shows all tools with configured limits.

### Checking Who's in Cooldown

```
/admin limit check
```

Lists users currently in cooldown and when they can sign out again.

### Clearing a User's Cooldown

```
/admin limit clear user:<username>
```

Resets their consecutive counter and cooldown for the current tool.

---

## User Exemptions

Grant specific users exemption from consecutive signout limits.

### Add Exemption

In the tool's signout channel:
```
/admin exempt add user:<username>
```

User can now sign out this tool unlimited times without cooldowns.

### Remove Exemption

```
/admin exempt remove user:<username>
```

### View All Exemptions

```
/admin exempt list
```

---

## Notifications & Monitoring

### Test Notification System

```
/testnotify
```

Sends a test notification to verify the system is working.

### Daily Summary

```
/adminsummary
```

Sends a summary of all active reservations to all admins.

---

## Common Admin Workflows

### New Tool Setup

1. Add the tool:
   ```
   /admin tool add name:new-tool max_hours:4 role_required:true
   ```

2. Assign initial users who are trained:
   ```
   /admin role assign user:username1
   /admin role assign user:username2
   ```

3. (Optional) Set consecutive limits if it's high-demand:
   ```
   /admin limit set max:3 cooldown:24
   ```

4. Announce the new tool in your announcement channel

### Scheduled Maintenance

1. Create an admin block:
   ```
   /admin block add tool:laser-cutter time:tomorrow 9am to 11am force:true
   ```

2. The bot will:
   - Cancel any existing reservations in that window (if force:true)
   - Prevent new reservations during that time
   - Notify affected users

3. After maintenance, remove the block:
   ```
   /admin block remove block:<select>
   ```

### Handling Photo Debt Issues

1. Check who has debts:
   ```
   /photoaudit
   ```

2. Contact the user and verify the photo or tool condition

3. Clear the debt:
   ```
   /clearphotodebt user:username
   ```

### User Not Returning Tool

1. Contact the user (DM or mention them)

2. If no response, force return:
   ```
   /admin reservation forcereturn
   ```

3. Tool is now available for others to reserve

### Tool Room Channel Setup

1. Create a Discord channel category named "Tool Room" (case-sensitive)

2. Move or create signout channels under that category

3. The bot automatically detects Tool Room channels and:
   - Requires photos for all signouts
   - Requires photos for all returns
   - Enforces photo grace periods and debts

**No additional configuration needed!**

---

## Tool Room Photo System

### How It Works

**For users signing out:**
- Immediate reservations (≤30 min): Photo required now
- Future reservations (>30 min): Photo can be sent via DM later
- Grace period: 10 minutes after start time
- Action: Reservation cancelled if no photo

**For users returning:**
- Photo should be attached with `/returntool`
- Grace period: 30 minutes after return
- Action: Photo debt created if no photo
- Consequence: Cannot sign out ANY Tool Room tools until debt cleared

### Admin Responsibilities

- Monitor photo debts with `/photoaudit`
- Verify photos when clearing debts
- Educate users on photo requirements
- Clear legitimate debts promptly

---

## Tips for Admins

- **Use admin blocks for maintenance** instead of manually clearing reservations
- **Set consecutive limits on popular tools** to ensure fair access
- **Enable role requirements** for safety-critical tools (saws, welders, etc.)
- **Monitor photo debts regularly** to keep users accountable
- **Exempt instructors** from consecutive limits if they need extended access
- **Use `/mystats user:<name>`** to check a user's history if needed
- **Tool Room = Automatic photo enforcement** (just put channels in that category)
- **Regular users can't clear photo debts** (prevents abuse)

---

## Emergency Procedures

### Bot Not Responding

1. Check bot status in server (online/offline)
2. Contact technical administrator
3. Check `#bot-logs` channel (if configured)

### Database Issues

1. Contact technical administrator immediately
2. Do not attempt database operations manually
3. Bot will log errors to console/logs

### User Dispute

1. Use `/mystats user:<username>` to check their history
2. Review reservation logs in the channel
3. Check photo evidence (if Tool Room)
4. Use admin override commands as needed
5. Document the resolution

---

## Getting Help

- **Command reference:** Type `/adminhelp` in Discord
- **User guide:** See `USER_QUICKSTART.md` for user-facing features
- **Technical docs:** See `README.md` and other docs in `/docs`
- **Backend issues:** Contact your technical administrator
- **Feature requests:** Discuss with technical team

---

## Command Quick Reference

All commands organized by function:

**Tool Management:**
- `/admin tool add/remove/maxtime`
- `/admin role toggle/assign/revoke/sync`

**Reservations:**
- `/admin reservation clear/forcereturn/adjust`
- `/admin block add/remove/list`

**Photo Management:**
- `/clearphotodebt user:<name>`
- `/photoaudit`
- `/photodebts tool:<name>`

**Limits & Access:**
- `/admin limit set/view/check/clear`
- `/admin exempt add/remove/list`

**Monitoring:**
- `/testnotify`
- `/adminsummary`
- `/debug logs tail/level/watch`
- `/mystats user:<name>`

