# Role-Based Permission System

## Overview

The bot now supports role-based permissions for tool signouts. Each tool can have its own Discord role, and users must have that role to sign out the tool. This provides fine-grained access control for your tools.

## Features

### Automatic Role Creation

- **New Tool Channels**: When a new `signout-*` channel is created, a role is automatically created
- **Manual Tool Addition**: Using `/addtool` automatically creates a role for the tool
- **Role Naming**: Roles are named `Tool: <tool_name>` (e.g., "Tool: 3D Printer")
- **Default State**: Role requirement is **disabled** by default, allowing admins to gradually implement

### Permission Checking

- **Signout Enforcement**: Users must have the tool's role to sign out (when enabled)
- **Admin Bypass**: Admins always bypass role requirements
- **Friendly Errors**: Users see helpful messages when they lack permissions
- **Role Mention**: Error messages include the role mention for easy identification

## Admin Commands

### `/togglerole`

Toggle whether a role is required to sign out the current tool.

**Usage**: Run in a tool channel (e.g., `#signout-3d-printer`)

**First Time**:
- Creates the role if it doesn't exist
- Enables the role requirement
- Confirms with role mention

**Subsequent Uses**:
- Toggles between enabled/disabled
- Shows current state

**Example**:
```
/togglerole
```

**Response**:
```
 Created role @Tool: 3D Printer for 3D Printer and enabled role requirement.

Users now need this role to sign out the tool.
```

### `/assignrole`

Give a user access to the current tool by assigning the role.

**Parameters**:
- `user`: The Discord member to grant access

**Usage**: Run in a tool channel

**Example**:
```
/assignrole user:@JohnDoe
```

**Response**:
```
 Granted @JohnDoe access to 3D Printer!

Role assigned: @Tool: 3D Printer
```

### `/revokerole`

Remove a user's access to the current tool by removing the role.

**Parameters**:
- `user`: The Discord member to revoke access from

**Usage**: Run in a tool channel

**Example**:
```
/revokerole user:@JohnDoe
```

**Response**:
```
 Revoked @JohnDoe's access to 3D Printer.

Role removed: @Tool: 3D Printer
```

### `/syncroles`

Sync all tool roles with the database. Creates roles for tools that don't have them and updates role IDs.

**Usage**: Can be run anywhere

**Example**:
```
/syncroles
```

**Response**: Shows an embed with:
- Created roles (new roles)
- Updated roles (ID changes)
- Errors (permission issues)

## Database Schema

### ToolModel Updates

Two new columns added:

```python
role_id = Column(String(50), nullable=True)        # Discord role ID
role_required = Column(Boolean, default=False)     # Whether role is required
```

### Repository Methods

New methods in `ToolRepository`:

```python
def set_role(self, tool_name: str, role_id: str, required: bool) -> bool
def get_role_id(self, tool_name: str) -> Optional[str]
def is_role_required(self, tool_name: str) -> bool
```

## Utilities

### discord_utils.py Functions

```python
async def create_tool_role(guild: discord.Guild, tool_name: str) -> Optional[discord.Role]
    # Creates a new role for a tool

def user_has_tool_role(user: discord.Member, role_id: str) -> bool
    # Checks if user has the required role (admins always return True)

async def get_or_create_tool_role(guild: discord.Guild, tool_name: str) -> Optional[discord.Role]
    # Gets existing role or creates it
```

## Implementation Flow

### New Tool Creation

1. Tool is created (via `/addtool` or channel creation)
2. Discord role is automatically created: `Tool: <tool_name>`
3. Role is linked to tool in database (`role_id` stored)
4. Role requirement is **disabled** by default (`role_required = False`)
5. Admin sees confirmation with role mention

### Signout Process with Roles

1. User runs `/signout` in a tool channel
2. Bot retrieves tool from database
3. **Role check** (if `role_required = True`):
   - Check if user is admin → Allow
   - Check if user has `role_id` → Allow/Deny
   - Show error with role mention if denied
4. Consecutive signout limit check
5. Time validation and conflict checking
6. Create reservation

### Enabling Role Requirements

1. Admin runs `/togglerole` in tool channel
2. If no role exists → Create and enable
3. If role exists → Toggle `role_required` flag
4. Update database
5. Confirm new state to admin

### Granting Access

1. Admin runs `/assignrole user:@Member` in tool channel
2. Bot retrieves tool's role_id
3. Assigns role to user
4. Logs action
5. Confirms to admin

## Migration

### For Existing Installations

1. **Database Migration**: Columns are nullable, so existing tools work without roles
2. **Run `/syncroles`**: Creates roles for all existing tools (disabled by default)
3. **Gradual Enablement**: Use `/togglerole` in each tool channel to enable as needed
4. **Assign Roles**: Use `/assignrole` to grant access to specific users

### For New Installations

- All tools automatically get roles created
- Roles are disabled by default
- Enable per-tool as needed

## Use Cases

### Public Tools

Tools anyone can use:
- Keep `role_required = False` (default)
- No permission checking occurs

### Restricted Tools

Tools requiring training/certification:
- Run `/togglerole` to enable requirement
- Use `/assignrole` to grant access to trained users
- Users see clear error messages if they lack access

### Admin-Only Tools

Highly restricted or dangerous tools:
- Enable role requirement
- Only assign role to admins/specific users
- Admins always bypass checks anyway

### Tiered Access

Different tools with different access levels:
- Create multiple tool roles
- Assign role combinations for different user tiers
- E.g., "Beginners" get basic tools, "Advanced" get all tools

## Error Messages

### Missing Role

When a user lacks the required role:

```
❌ You need the @Tool: 3D Printer role to sign out 3D Printer.

Please contact an admin to get access to this tool.
```

### No Role Configured

When trying to manage a tool without a role:

```
❌ 3D Printer doesn't have a role configured.

Use /togglerole first to create and enable the role.
```

## Permissions Required

### Bot Permissions

The bot needs these permissions to manage roles:

- **Manage Roles**: To create, assign, and remove roles
- **Role Hierarchy**: Bot's role must be higher than tool roles

### Best Practices

1. **Bot Role Position**: Place the bot's role high in the hierarchy
2. **Tool Role Position**: Tool roles can be near the bottom
3. **Color Coding**: Tool roles use blue by default, customize as needed
4. **Hoisting**: Tool roles don't hoist (display separately) by default

## Logging

All role operations are logged:

```python
logger.info(f"Created and enabled role requirement for {tool_name} (role ID: {role.id})")
logger.info(f"{admin_name} assigned {role_name} to {user_name} for {tool_name}")
logger.info(f"Role requirement blocked {username} from signing out {tool_name}")
```

## Future Enhancements

Potential additions:

- Role groups (e.g., "CNC Machines" role for multiple tools)
- Time-limited role grants (temporary access)
- Role request system (users request access, admins approve)
- Automatic role assignment based on criteria
- Integration with training/certification tracking

## Troubleshooting

### Role Not Found

**Symptom**: "Role not found. It may have been deleted."

**Solution**: Run `/syncroles` to recreate roles

### Permission Denied

**Symptom**: "I don't have permission to assign roles."

**Solution**: Check bot's role hierarchy and permissions

### Role Already Exists

**Symptom**: Bot finds existing role instead of creating new one

**Behavior**: This is normal! Bot reuses existing roles with matching names

### Wrong Role Assigned

**Symptom**: Bot linked wrong role to tool

**Solution**: Delete the incorrect role, then run `/syncroles` to recreate

## Technical Notes

### Role Storage

- Role IDs are stored as strings (Discord snowflakes are 64-bit)
- Nullable field allows backward compatibility
- Boolean flag (`role_required`) separates "has role" from "requires role"

### Admin Bypass

Admins always bypass role checks:

```python
if user_is_admin(interaction.user):
    return True
```

This ensures admins can always manage tools regardless of their roles.

### Role Naming Convention

Format: `Tool: <tool_name>`

- Prefix clearly identifies tool roles
- Spaces in tool names are preserved
- Case-sensitive matching

## Examples

### Example 1: Restrict 3D Printer

```bash
# In #signout-3d-printer channel
/togglerole
# Response:  Created role @Tool: 3D Printer for 3D Printer and enabled role requirement.

# Grant access to user
/assignrole user:@Alice
# Response:  Granted @Alice access to 3D Printer!

# Now Alice can sign out, others see:
# ❌ You need the @Tool: 3D Printer role to sign out 3D Printer.
```

### Example 2: Migrate All Tools

```bash
# Create roles for all existing tools
/syncroles
# Response: Embed showing created roles

# Selectively enable restrictions
# In #signout-laser-cutter
/togglerole
# In #signout-cnc-mill
/togglerole

# Basic tools remain unrestricted (leave togglerole off)
```

### Example 3: Remove Access

```bash
# User violated policy, remove access
/revokerole user:@BadUser
# Response:  Revoked @BadUser's access to 3D Printer.

# User attempts signout:
# ❌ You need the @Tool: 3D Printer role to sign out 3D Printer.
```

## Summary

The role-based permission system provides flexible, fine-grained access control for tools while maintaining ease of use. Roles are created automatically, disabled by default, and can be enabled per-tool as needed. The system integrates seamlessly with existing functionality and maintains admin bypass for convenience.
