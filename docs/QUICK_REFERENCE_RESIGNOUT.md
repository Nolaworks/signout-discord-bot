# Quick Reference: Re-Signout Limits

## Commands At A Glance

| Command | Purpose | Example |
|---------|---------|---------|
| `/setresignoutlimit` | Set limit for current tool | `/setresignoutlimit max_consecutive:3 cooldown_hours:24` |
| `/viewresignoutlimits` | View all configured limits | `/viewresignoutlimits` |
| `/checkcooldowns` | See who's in cooldown | `/checkcooldowns` |
| `/clearcooldown` | Reset user's cooldown | `/clearcooldown username:john` |

## Quick Setup Guide

### Step 1: Choose Your Tool
Go to the tool's channel (e.g., `#signout-3d-printer`)

### Step 2: Set the Limit
```
/setresignoutlimit max_consecutive:3 cooldown_hours:24
```

### Step 3: Announce to Users
"📢 New policy: You can sign out the 3D Printer up to 3 times in a row. After that, there's a 24-hour cooldown to give others a chance."

## Recommended Settings

| Tool Type | Max Consecutive | Cooldown | Reasoning |
|-----------|----------------|----------|-----------|
| 3D Printer | 2-3 | 24h | High demand |
| Laser Cutter | 2-3 | 24h | High demand |
| CNC Router | 3-4 | 12h | Medium demand |
| Hand Tools | 0 (no limit) | - | Low demand |
| Specialty Equipment | 4-5 | 24h | Few users |

## Common Scenarios

### "User needs emergency access"
```
/clearcooldown username:emergency_user
```

### "Disable limits temporarily"
```
/setresignoutlimit max_consecutive:0 cooldown_hours:1
```

### "Check if limits are working"
```
/checkcooldowns
```

### "See all configured tools"
```
/viewresignoutlimits
```

## User Error Messages

When a user hits their limit:
```
🚫 Maximum Consecutive Signouts Reached

You've signed out 3D Printer 3 times in a row.

To give others a chance, you must wait 24 hours 
before signing it out again.

You can sign out after:
11/25/2025 at 02:30 PM CT
```

## Important Notes

 **Admins are exempt** - You can always sign out
 **Auto-reset** - Count resets when others use the tool
 **Tool-specific** - Each tool has its own limit
 **No limit by default** - Must explicitly configure

## Troubleshooting

**Limits not enforcing?**
1. Check if configured: `/viewresignoutlimits`
2. Verify user isn't an admin
3. Run migration: `python3 migrate_consecutive_signouts.py`

**Need to reset someone?**
```
/clearcooldown username:their_username
```

**Want to change limit?**
Just run `/setresignoutlimit` again with new values

## Best Practices

1. **Start conservative** - Try 2-3 consecutive, adjust based on feedback
2. **Monitor usage** - Check `/checkcooldowns` weekly
3. **Communicate clearly** - Announce limits before enforcing
4. **Be flexible** - Use `/clearcooldown` for legitimate needs
5. **Review quarterly** - Adjust limits as tool usage patterns change
