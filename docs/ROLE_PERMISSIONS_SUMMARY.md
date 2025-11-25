# Role-Based Permissions Feature - Implementation Summary

## Overview

Implemented a comprehensive role-based permission system that allows fine-grained access control for tool signouts. Each tool can have its own Discord role, and users must have that role to sign out the tool.

## What Was Added

### Database Changes

**New columns in `tools` table:**
- `role_id` (VARCHAR(50), nullable) - Stores Discord role ID
- `role_required` (BOOLEAN, default FALSE) - Whether role check is enforced

### Code Changes

#### discord_utils.py
Added three new utility functions:
- `create_tool_role(guild, tool_name)` - Creates Discord role for tool
- `user_has_tool_role(user, role_id)` - Checks if user has required role
- `get_or_create_tool_role(guild, tool_name)` - Gets existing or creates new role

#### repositories.py
Added three new methods to `ToolRepository`:
- `set_role(tool_name, role_id, required)` - Links role to tool
- `get_role_id(tool_name)` - Gets role ID for tool
- `is_role_required(tool_name)` - Checks if role is required

#### mainbot.py
- **Signout command**: Added role permission check before allowing signout
- **Channel creation**: Auto-creates role when new tool channel is detected
- Both admins and users with correct role can sign out

#### admin_panel.py
Added five new admin commands:
1. `/togglerole` - Enable/disable role requirement for current tool
2. `/assignrole user:<member>` - Give user access to current tool
3. `/revokerole user:<member>` - Remove user's access to current tool
4. `/syncroles` - Create roles for all tools in database
5. Updated `/addtool` - Automatically creates role when tool is added

### Migration Script

**`helper_scripts/migrate_role_permissions.py`:**
- Adds `role_id` and `role_required` columns to existing database
- Safe to run multiple times (checks if columns exist)
- Includes verification step

### Documentation

**`ROLE_BASED_PERMISSIONS.md`:**
- Comprehensive guide covering all features
- Command reference with examples
- Migration instructions
- Troubleshooting guide
- Use cases and best practices

## Key Features

### Automatic Role Creation
- Roles created automatically when tools are added
- Naming convention: `Tool: <tool_name>`
- Roles disabled by default (backward compatible)
- Blue color, non-hoisted by default

### Permission Enforcement
- Checked during signout command
- Admins always bypass checks
- Clear error messages with role mentions
- Blocks signout if user lacks role

### Admin Control
- Toggle role requirement per tool
- Assign/revoke roles from users
- Bulk sync all tools at once
- View role status in commands

## User Experience

### For Regular Users
```
User attempts signout without role:
❌ You need the @Tool: 3D Printer role to sign out 3D Printer.

Please contact an admin to get access to this tool.
```

### For Admins

**Enabling role requirement:**
```
/togglerole
 Created role @Tool: 3D Printer for 3D Printer and enabled role requirement.

Users now need this role to sign out the tool.
```

**Granting access:**
```
/assignrole user:@Alice
 Granted @Alice access to 3D Printer!

Role assigned: @Tool: 3D Printer
```

**Bulk sync:**
```
/syncroles
[Embed showing created/updated roles for all tools]
```

## Migration Path

### For Existing Installations

1. **Backup database:**
   ```bash
   ./helper_scripts/backup_db.sh
   ```

2. **Run migration:**
   ```bash
   python3 helper_scripts/migrate_role_permissions.py
   ```

3. **Deploy updated code:**
   ```bash
   # Pull latest code
   git pull
   
   # Restart bot
   systemctl restart signout-bot
   ```

4. **Create roles in Discord:**
   ```
   /syncroles
   ```

5. **Enable per tool as needed:**
   ```
   /togglerole (in each tool channel)
   ```

6. **Assign roles to users:**
   ```
   /assignrole user:@Member
   ```

### For New Installations

- Everything works automatically
- Roles created when tools are added
- Admins can enable/disable per tool
- No migration needed

## Backward Compatibility

- **Nullable columns**: Existing tools work without roles
- **Default disabled**: Role checking off by default
- **Graceful fallback**: If role doesn't exist, allow access
- **Admin bypass**: Admins always have access

## Testing Checklist

- [x] Import all modules without errors
- [ ] Run migration script on test database
- [ ] Create new tool, verify role is created
- [ ] Toggle role requirement on/off
- [ ] Assign role to user, verify they can sign out
- [ ] Remove role from user, verify signout is blocked
- [ ] Admin can sign out regardless of role
- [ ] Run /syncroles with existing tools
- [ ] Check error messages display correctly

## Configuration

### Bot Permissions Needed
- **Manage Roles** - To create/assign/remove roles
- **Role Hierarchy** - Bot role must be above tool roles

### Environment Variables
No new environment variables needed - uses existing database connection.

### Config Settings
No new config settings needed - uses existing settings.

## Performance Impact

- **Database**: Two additional nullable columns (minimal)
- **Queries**: One additional check per signout (fast lookup)
- **Discord API**: Role creation/assignment (only when needed)
- **Memory**: Minimal - roles cached by Discord.py

## Security Considerations

- **Admin bypass**: Intentional design for flexibility
- **Role hierarchy**: Bot must have Manage Roles permission
- **Permission errors**: Gracefully handled with user-friendly messages
- **Audit trail**: All role operations logged

## Known Limitations

1. **Role deletion**: If role is deleted from Discord, users can still sign out until role_required is toggled
2. **Manual role changes**: Bot doesn't detect manual role assignments outside commands
3. **Role position**: Bot role must be higher than tool roles in hierarchy
4. **Name changes**: Changing tool name doesn't automatically rename role

## Future Enhancements

Possible additions for future versions:

- **Role groups**: One role for multiple related tools
- **Time-limited access**: Temporary role assignments
- **Role requests**: User-initiated access requests
- **Training integration**: Link role assignment to training completion
- **Audit log**: Discord channel logging of role changes
- **Bulk operations**: Assign/revoke roles for multiple users at once

## Files Modified

### Core Files
- `database.py` - Added role columns to ToolModel
- `repositories.py` - Added role methods to ToolRepository
- `discord_utils.py` - Added role utility functions
- `mainbot.py` - Added role check to signout, auto-create on channel creation
- `admin_panel.py` - Added 5 role management commands

### Scripts
- `helper_scripts/migrate_role_permissions.py` - New migration script
- `helper_scripts/helper_README.md` - Updated with new script

### Documentation
- `ROLE_BASED_PERMISSIONS.md` - Comprehensive feature documentation
- `ROLE_PERMISSIONS_SUMMARY.md` - This file

## Command Reference

| Command | Description | Parameters |
|---------|-------------|------------|
| `/togglerole` | Enable/disable role requirement | None (use in tool channel) |
| `/assignrole` | Give user tool access | `user:<member>` |
| `/revokerole` | Remove user's tool access | `user:<member>` |
| `/syncroles` | Create roles for all tools | None |
| `/addtool` | Add tool (auto-creates role) | `tool:<name>` `max_hours:<number>` |

## Support

For issues or questions:
1. Check `ROLE_BASED_PERMISSIONS.md` for detailed documentation
2. Review bot logs for error messages
3. Verify bot has Manage Roles permission
4. Check role hierarchy (bot role above tool roles)

## Credits

Implemented as part of the Discord signout bot project. Integrates seamlessly with existing consecutive signout limits and admin exemption features.
