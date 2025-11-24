# Consecutive Re-Signout Limits Feature

## Overview

This feature prevents users from monopolizing tools by limiting how many times they can sign out the same tool consecutively. After reaching the limit, users must wait through a cooldown period before they can sign out that tool again.

## How It Works

### For Users

1. **Normal Usage**: Sign out tools as usual with `/signout`
2. **Consecutive Tracking**: Each time you return/complete a reservation, the system tracks if you sign it out again
3. **Limit Reached**: After reaching the configured limit (e.g., 3 consecutive signouts), you'll be blocked from signing out that tool
4. **Cooldown Period**: You must wait the configured cooldown period (e.g., 24 hours) before you can sign out that tool again
5. **Reset**: If someone else signs out the tool, your consecutive count resets to zero

### Example Scenario

**Tool Configuration:**
- Max consecutive signouts: 3
- Cooldown period: 24 hours

**User Timeline:**
1. Monday 9am: User signs out 3D Printer → Count: 1
2. Monday 2pm: User returns and immediately signs out again → Count: 2
3. Monday 5pm: User returns and immediately signs out again → Count: 3
4. Monday 8pm: User tries to sign out again → **BLOCKED** (cooldown active)
5. Tuesday 8pm: Cooldown expires, user can sign out again

**Reset Scenario:**
- If another user signs out the 3D Printer between Monday 5pm and 8pm, the first user's count resets to 0

## Admin Commands

### `/setresignoutlimit`
Configure consecutive signout limits for the current tool.

**Parameters:**
- `max_consecutive` (required): Maximum times a user can sign out this tool in a row
  - Set to `0` to disable limits (unlimited signouts)
  - Typical values: 2-5
- `cooldown_hours` (required): Hours user must wait after reaching limit
  - Minimum: 1 hour
  - Typical values: 12-48 hours

**Usage:**
```
/setresignoutlimit max_consecutive:3 cooldown_hours:24
```

**Example:**
In the `#signout-laser-cutter` channel:
```
/setresignoutlimit max_consecutive:2 cooldown_hours:12
```
This sets the laser cutter to allow 2 consecutive signouts, then requires a 12-hour cooldown.

---

### `/viewresignoutlimits`
View all tools that have consecutive signout limits configured.

**Usage:**
```
/viewresignoutlimits
```

**Output:**
Shows a list of all tools with limits, including:
- Tool name
- Maximum consecutive signouts allowed
- Cooldown period in hours
- Last updated date

---

### `/checkcooldowns`
View users currently in cooldown or approaching the limit for the current tool.

**Usage:**
In a tool channel:
```
/checkcooldowns
```

**Output:**
- Users currently in cooldown with time remaining
- Users who have consecutive signouts but haven't reached the limit yet

---

### `/clearcooldown`
Reset a specific user's cooldown and consecutive count for the current tool.

**Parameters:**
- `username` (required): Username to clear (autocompletes)

**Usage:**
```
/clearcooldown username:john_doe
```

**Use Cases:**
- User had an emergency and needs immediate access
- Resolving disputes or special circumstances
- Testing the system

## Technical Details

### Database Tables

**`tool_signout_limits`**
- Stores per-tool configuration
- `max_consecutive_signouts`: Limit (0 = no limit)
- `cooldown_hours`: Cooldown period

**`consecutive_signout_tracker`**
- Tracks each user's consecutive signouts per tool
- `consecutive_count`: Current consecutive count
- `last_signout_ended_at`: When their last reservation ended
- `cooldown_expires_at`: When cooldown ends (null if not in cooldown)

### Behavior Rules

1. **Admins are exempt** - Admin users bypass all consecutive signout limits
2. **Tool-specific** - Limits are configured per tool, not globally
3. **Automatic reset** - If another user signs out the tool, previous users' counts reset
4. **Return triggers count** - Consecutive count increments when signing out, not when returning
5. **Cooldown enforcement** - Cooldown is checked before allowing a new signout

### Migration

To add these features to an existing database:

```bash
python3 helper_scripts/migrate_consecutive_signouts.py
```

This creates the new tables without affecting existing data.

## Best Practices

### Setting Limits

**High-demand tools** (3D printers, laser cutters):
- Max consecutive: 2-3
- Cooldown: 24-48 hours
- Reasoning: Give more people access to popular equipment

**Specialized tools** (expensive or rarely used):
- Max consecutive: 3-5
- Cooldown: 12-24 hours
- Reasoning: Fewer users need these, but prevent monopolization

**Low-demand tools** (hand tools):
- No limit (max_consecutive: 0)
- Reasoning: Plenty of availability, no need to restrict

### Monitoring

1. **Weekly review**: Check `/viewresignoutlimits` to see configured tools
2. **User feedback**: Adjust limits based on user complaints about availability
3. **Cooldown checks**: Use `/checkcooldowns` to see if limits are working
4. **Clear exceptions**: Use `/clearcooldown` for legitimate emergencies only

### Communication

When setting up limits:
1. Announce in Discord that limits are being implemented
2. Explain the reasoning (fair access, prevent monopolization)
3. Post the specific limits for each tool
4. Remind users that admins can grant exceptions if needed

## Troubleshooting

**User can't sign out but should be able to:**
1. Check `/checkcooldowns` to see their status
2. Verify limit configuration with `/viewresignoutlimits`
3. Use `/clearcooldown username:user` to reset if appropriate
4. Check bot logs for errors

**Limits not working:**
1. Confirm limits are set: `/viewresignoutlimits`
2. Verify user is not an admin (admins bypass limits)
3. Check database migration was run
4. Review bot logs for errors

**False positives (counting when shouldn't):**
1. Check if another user actually signed out between their reservations
2. Use `/clearcooldown` to reset the user
3. Report issue for investigation

## Future Enhancements

Potential improvements to consider:

- [ ] Global cooldown settings (apply to all tools)
- [ ] Different limits for different user roles
- [ ] Notifications when user approaches limit
- [ ] Statistics on how often limits are hit
- [ ] Automatic limit adjustment based on demand
- [ ] Exemption list for specific users
- [ ] Warning message on 2nd-to-last signout
