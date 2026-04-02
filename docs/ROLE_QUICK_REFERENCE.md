# Role-Based Permissions - Quick Reference

## Quick Start

### First Time Setup
1. Run `/syncroles` to create roles for all tools
2. Go to each restricted tool's channel
3. Run `/togglerole` to enable role requirement
4. Use `/assignrole` to grant access to users

## Admin Commands Reference

### `/syncroles`
**Where:** Anywhere  
**What:** Creates Discord roles for all tools in database  
**Use when:** Initial setup, after adding many tools manually, fixing missing roles

```
/syncroles
```

---

### `/togglerole`
**Where:** Tool channel (e.g., #signout-3d-printer)  
**What:** Enable/disable role requirement for current tool  
**Use when:** Making a tool restricted or unrestricted

```
# First use - creates role and enables requirement
/togglerole

# Second use - disables requirement
/togglerole

# Third use - re-enables requirement
/togglerole
```

---

### `/assignrole user:<member>`
**Where:** Tool channel  
**What:** Give a user access to the current tool  
**Use when:** User needs permission to sign out the tool

```
/assignrole user:@Alice
/assignrole user:@Bob
```

---

### `/revokerole user:<member>`
**Where:** Tool channel  
**What:** Remove user's access to the current tool  
**Use when:** Revoking someone's permission

```
/revokerole user:@Alice
```

---

### `/addtool tool:<name> max_hours:<number>`
**Where:** Anywhere  
**What:** Add new tool (automatically creates role)  
**Use when:** Adding a new tool to the system

```
/addtool tool:Laser Cutter max_hours:4
# Role "Tool: Laser Cutter" created automatically (disabled)
```

## Common Workflows

### Make a Tool Restricted

1. Go to tool's channel: `#signout-3d-printer`
2. Enable requirement:
   ```
   /togglerole
   ```
3. Grant access to trained users:
   ```
   /assignrole user:@Alice
   /assignrole user:@Bob
   /assignrole user:@Charlie
   ```

### Make a Tool Unrestricted

1. Go to tool's channel
2. Disable requirement:
   ```
   /togglerole
   ```
   (Role still exists but isn't checked)

### Grant Temporary Access

1. Assign role:
   ```
   /assignrole user:@User
   ```
2. When done, revoke:
   ```
   /revokerole user:@User
   ```

### New Tool Setup

When creating new tool:
```
/addtool tool:CNC Mill max_hours:8
```
Role is created automatically (disabled)

To make it restricted:
1. Go to tool's channel
2. Run `/togglerole`
3. Assign roles to users

### Bulk User Access

To grant multiple users access to same tool:

1. Go to tool channel
2. Run assignrole for each:
   ```
   /assignrole user:@User1
   /assignrole user:@User2
   /assignrole user:@User3
   ```

## User Experience

### User WITHOUT required role tries to sign out:
```
❌ You need the @Tool: 3D Printer role to sign out 3D Printer.

Please contact an admin to get access to this tool.
```

### User WITH required role:
```
 Signed out 3D Printer for now for 2 hours by Alice — Friday 2pm-4pm
```

### Admin (always works):
```
 Signed out 3D Printer for now for 2 hours by AdminUser — Friday 2pm-4pm
```

## Checking Status

### See if tool has role requirement:
1. Go to tool channel
2. Look at channel topic or run a test command
3. Or check server roles for "Tool: <name>"

### See who has access:
1. Server Settings → Roles
2. Find "Tool: <name>" role
3. Click to see members with that role

### Verify role exists:
1. Server Settings → Roles
2. Look for "Tool: <name>"
3. Or run `/syncroles` to recreate if missing

## Troubleshooting

### "I don't have permission to assign roles"
- Check bot has "Manage Roles" permission
- Ensure bot's role is above tool roles in hierarchy
- Server Settings → Roles → Drag bot role up

### "Role not found"
Run `/syncroles` to recreate roles

### User says they have role but can't sign out
- Check role requirement is enabled (`/togglerole`)
- Verify user actually has the role in Discord
- Check if it's the correct role (same name)

### Role requirement won't toggle
- Verify you're in the correct tool channel
- Check bot has permissions
- Look at bot logs for errors

## Best Practices

### Gradual Rollout
- Start with 1-2 restricted tools
- Get user feedback
- Expand to more tools as needed

### Clear Communication
- Tell users which tools are restricted
- Explain how to request access
- Document your access policy

### Regular Audits
- Review who has access to each tool
- Remove roles from users who no longer need them
- Check for unused roles

### Naming Consistency
- Roles are auto-named: "Tool: <name>"
- Don't rename them manually
- Let bot recreate if needed

## Technical Notes

### Role Properties
- **Name:** `Tool: <tool_name>`
- **Color:** Blue (#0000FF)
- **Hoisted:** No (doesn't display separately)
- **Mentionable:** No
- **Created by:** Bot automatically

### Database
- Roles stored in `tools` table
- `role_id` column: Discord role ID
- `role_required` column: Boolean flag

### Admin Bypass
Admins ALWAYS bypass role checks:
- Can sign out any tool regardless of role
- Useful for emergency situations
- Based on Discord admin permission

## Emergency: Disable All Restrictions

If needed, disable all role requirements:

```python
# In bot's database
UPDATE tools SET role_required = FALSE;
```

Then restart bot. All tools become unrestricted.

## Quick Checklist

New Restricted Tool:
- [ ] Tool exists in database
- [ ] Role created (automatic)
- [ ] Role requirement enabled (`/togglerole`)
- [ ] Users assigned roles (`/assignrole`)
- [ ] Tested with user account
- [ ] Tested that non-users are blocked

Removing Restrictions:
- [ ] Go to tool channel
- [ ] Run `/togglerole` to disable
- [ ] Test that anyone can sign out
- [ ] (Optional) Remove roles from users

## Support

**Full Documentation:** `ROLE_BASED_PERMISSIONS.md`

**Migration Guide:** `ROLE_DEPLOYMENT_CHECKLIST.md`

**Summary:** `ROLE_PERMISSIONS_SUMMARY.md`

**Helper Scripts:** `helper_scripts/helper_README.md`

## Command Summary Table

| Command | Location | Purpose | Example |
|---------|----------|---------|---------|
| `/syncroles` | Anywhere | Create all tool roles | `/syncroles` |
| `/togglerole` | Tool channel | Enable/disable requirement | `/togglerole` |
| `/assignrole` | Tool channel | Grant access | `/assignrole user:@Alice` |
| `/revokerole` | Tool channel | Revoke access | `/revokerole user:@Alice` |
| `/addtool` | Anywhere | Add tool (creates role) | `/addtool tool:Mill max_hours:8` |

---

**Last Updated:** 2025-11-24  
**Version:** 1.0
