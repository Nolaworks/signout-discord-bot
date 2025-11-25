# Phase 4: Role-Based Tool Access Control

**Date:** November 24, 2025  
**Status:** 🎨 DESIGN PHASE  
**Priority:** HIGH for security-sensitive tools

---

## 🎯 Overview

Implement Discord role-based permission system to restrict access to specific tools. This allows administrators to control who can use expensive, dangerous, or specialized equipment.

---

## 💡 Concept

### The Problem
Currently, any server member can sign out any tool. This creates risks:
- Untrained users accessing dangerous equipment (laser cutter, CNC)
- Unauthorized use of expensive tools
- No way to enforce training/certification requirements
- No accountability for specialized equipment

### The Solution
**Role-based access control:**
- Each tool can have **required roles**
- Users must have at least ONE required role to sign out the tool
- Admins can manage role requirements through commands
- Clear error messages when access is denied

---

## 🏗️ Architecture

### Database Schema

```sql
-- New table for tool role requirements
CREATE TABLE tool_roles (
    id SERIAL PRIMARY KEY,
    tool_id INTEGER NOT NULL,
    role_id VARCHAR(50) NOT NULL,  -- Discord role ID
    role_name VARCHAR(100) NOT NULL,
    required BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(50),  -- admin user_id who added this requirement
    FOREIGN KEY (tool_id) REFERENCES tools(id) ON DELETE CASCADE,
    UNIQUE(tool_id, role_id)
);

-- Index for fast lookups
CREATE INDEX idx_tool_roles_tool_id ON tool_roles(tool_id);
CREATE INDEX idx_tool_roles_role_id ON tool_roles(role_id);

-- Add to tools table
ALTER TABLE tools ADD COLUMN requires_roles BOOLEAN DEFAULT FALSE;
ALTER TABLE tools ADD COLUMN open_access BOOLEAN DEFAULT TRUE;
```

### Access Control Levels

**Level 1: Open Access (Default)**
- `open_access = True`
- `requires_roles = False`
- Anyone can sign out

**Level 2: Role Required**
- `open_access = False`
- `requires_roles = True`
- Must have at least one required role

**Level 3: Admin Only**
- Special flag or specific admin role
- Only server admins can sign out

---

## 🎭 Role Management

### Suggested Role Structure

```
Tool Access Roles:
├── @Basic Tools Access (hand tools, simple equipment)
├── @Power Tools Certified (drills, saws, sanders)
├── @CNC Operator (CNC machines)
├── @Laser Cutter Certified (laser equipment)
├── @3D Printer User (3D printers)
├── @Welding Certified (welding equipment)
└── @Master Maker (full shop access)
```

### Role Assignment Workflow

1. **New Member:** Gets @Basic Tools Access by default
2. **Training Session:** Completes safety training → Role granted
3. **Certification:** Passes competency test → Advanced role granted
4. **Violation:** Role removed temporarily or permanently

---

## 🔧 Implementation

### Files to Create

**1. `permissions.py` - Permission checking**
```python
"""
Permission and role-based access control.
"""
from typing import List, Optional
import discord
from exceptions import PermissionError

async def check_tool_access(user: discord.Member, tool_id: int) -> tuple[bool, Optional[str]]:
    """
    Check if user has permission to access a tool.
    
    Returns:
        (has_access: bool, error_message: Optional[str])
    """
    # Get tool role requirements from database
    required_roles = get_tool_required_roles(tool_id)
    
    if not required_roles:
        return True, None  # Open access tool
    
    # Check if user has any required role
    user_role_ids = [str(role.id) for role in user.roles]
    required_role_ids = [r.role_id for r in required_roles]
    
    has_access = any(role_id in required_role_ids for role_id in user_role_ids)
    
    if not has_access:
        role_names = [r.role_name for r in required_roles]
        error_msg = (
            f"🚫 Access Denied: This tool requires one of the following roles:\n"
            f"{', '.join(f'@{name}' for name in role_names)}\n\n"
            f"Contact an admin to gain access after completing training."
        )
        return False, error_msg
    
    return True, None
```

**2. Update `repositories.py` - Add ToolRoleRepository**
```python
class ToolRoleRepository:
    """Repository for tool role requirements"""
    
    def add_role_requirement(self, tool_id: int, role_id: str, role_name: str, 
                           added_by: str) -> ToolRoleModel:
        """Add a role requirement to a tool"""
        
    def remove_role_requirement(self, tool_id: int, role_id: str) -> bool:
        """Remove a role requirement from a tool"""
        
    def get_tool_roles(self, tool_id: int) -> List[ToolRoleModel]:
        """Get all required roles for a tool"""
        
    def get_roles_for_user_tools(self, user_role_ids: List[str]) -> List[int]:
        """Get tool IDs accessible by user with given roles"""
```

### Commands to Add

**Admin Commands:**

```python
@bot.tree.command(name="requirerole")
@app_commands.describe(
    tool="Tool to restrict",
    role="Discord role required to use this tool"
)
@is_admin()
async def require_role(interaction: discord.Interaction, tool: str, role: discord.Role):
    """
    Add a role requirement to a tool.
    
    Example: /requirerole tool:CNC role:@CNC Operator
    """
    # Add role requirement to database
    # Set tool.requires_roles = True
    # Set tool.open_access = False
    
    await interaction.response.send_message(
        f" Tool `{tool}` now requires role @{role.name}",
        ephemeral=True
    )

@bot.tree.command(name="removerole")
@app_commands.describe(
    tool="Tool to unrestrict",
    role="Role requirement to remove"
)
@is_admin()
async def remove_role(interaction: discord.Interaction, tool: str, role: discord.Role):
    """Remove a role requirement from a tool."""
    # Remove from database
    # If no roles left, set open_access = True
    
    await interaction.response.send_message(
        f" Removed role requirement @{role.name} from `{tool}`",
        ephemeral=True
    )

@bot.tree.command(name="listroles")
@app_commands.describe(tool="Tool to check (optional)")
async def list_roles(interaction: discord.Interaction, tool: str = None):
    """
    List role requirements for tools.
    
    If tool specified: Show roles required for that tool
    If no tool: Show all tools with role requirements
    """
    if tool:
        # Show specific tool's roles
        roles = get_tool_roles(tool)
        if not roles:
            await interaction.response.send_message(
                f"Tool `{tool}` has no role requirements (open access)",
                ephemeral=True
            )
        else:
            role_list = "\n".join(f"• @{r.role_name}" for r in roles)
            await interaction.response.send_message(
                f"**Tool: {tool}**\nRequired roles:\n{role_list}",
                ephemeral=True
            )
    else:
        # Show all restricted tools
        embed = discord.Embed(title="🔒 Tool Access Requirements")
        restricted_tools = get_all_restricted_tools()
        for tool in restricted_tools:
            roles = get_tool_roles(tool.name)
            role_names = ", ".join(f"@{r.role_name}" for r in roles)
            embed.add_field(name=tool.name, value=role_names, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="openaccess")
@app_commands.describe(tool="Tool to make openly accessible")
@is_admin()
async def open_access(interaction: discord.Interaction, tool: str):
    """Remove all role requirements from a tool (make it open access)."""
    # Remove all role requirements
    # Set open_access = True, requires_roles = False
    
    await interaction.response.send_message(
        f" Tool `{tool}` is now open access (no role requirements)",
        ephemeral=True
    )
```

**User Commands:**

```python
@bot.tree.command(name="myaccess")
async def my_access(interaction: discord.Interaction):
    """
    Show which tools you can access based on your roles.
    """
    user_roles = [str(role.id) for role in interaction.user.roles]
    accessible_tools = get_accessible_tools(user_roles)
    restricted_tools = get_all_tools() - accessible_tools
    
    embed = discord.Embed(title=f"🔑 Tool Access for {interaction.user.name}")
    
    if accessible_tools:
        accessible_list = "\n".join(f" {tool.name}" for tool in accessible_tools)
        embed.add_field(name="Accessible Tools", value=accessible_list, inline=False)
    
    if restricted_tools:
        restricted_list = "\n".join(f"🔒 {tool.name}" for tool in restricted_tools)
        embed.add_field(name="Restricted Tools", value=restricted_list, inline=False)
    
    embed.set_footer(text="Use /listroles to see what roles are required")
    await interaction.response.send_message(embed=embed, ephemeral=True)
```

### Modify Existing Commands

**Update `/signout` command:**
```python
@bot.tree.command(name="signout")
async def signout(interaction: discord.Interaction, time: str, photo: discord.Attachment = None):
    tool = interaction.channel.name
    
    # Check permissions BEFORE processing reservation
    has_access, error_msg = await check_tool_access(interaction.user, tool)
    if not has_access:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return
    
    # Continue with normal signout flow...
```

---

## 🎨 User Experience

### Success Flow
```
User: /signout time:tomorrow 2pm to 5pm
Bot:  Reserved CNC Router for tomorrow 2:00 PM - 5:00 PM
```

### Access Denied Flow
```
User: /signout time:tomorrow 2pm to 5pm
Bot: 🚫 Access Denied: This tool requires one of the following roles:
     • @CNC Operator
     • @Master Maker
     
     Contact an admin to gain access after completing training.
     Use /myaccess to see which tools you can use.
```

### Admin Management Flow
```
Admin: /requirerole tool:CNC role:@CNC Operator
Bot:  Tool `CNC` now requires role @CNC Operator

Admin: /listroles tool:CNC
Bot: **Tool: CNC**
     Required roles:
     • @CNC Operator
     • @Master Maker

Admin: /openaccess tool:Hand Drill
Bot:  Tool `Hand Drill` is now open access (no role requirements)
```

---

## 📊 Admin Dashboard Enhancements

### New Analytics

```python
@bot.tree.command(name="accessstats")
@is_admin()
async def access_stats(interaction: discord.Interaction):
    """Show statistics about tool access and role distribution."""
    
    stats = {
        'total_tools': count_all_tools(),
        'open_access_tools': count_open_access_tools(),
        'restricted_tools': count_restricted_tools(),
        'users_with_basic_access': count_users_with_role('@Basic Tools Access'),
        'users_with_advanced_access': count_users_with_role('@Master Maker'),
        'access_denied_count_today': count_access_denied_today()
    }
    
    embed = discord.Embed(title="🔐 Access Control Statistics")
    embed.add_field(name="Open Access Tools", value=stats['open_access_tools'])
    embed.add_field(name="Restricted Tools", value=stats['restricted_tools'])
    embed.add_field(name="Access Denials (Today)", value=stats['access_denied_count_today'])
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
```

---

## 🔒 Security Considerations

### Bypass Prevention
- Check permissions server-side (never trust client)
- Re-check permissions on every command
- Admin commands require admin role check
- Log all permission changes

### Audit Trail
```sql
CREATE TABLE permission_audit_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    admin_user_id VARCHAR(50),
    action VARCHAR(50), -- 'ADD_ROLE', 'REMOVE_ROLE', 'OPEN_ACCESS', 'ACCESS_DENIED'
    tool_id INTEGER,
    role_id VARCHAR(50),
    affected_user_id VARCHAR(50),
    details TEXT
);
```

### Emergency Override
- Admins can always bypass restrictions
- Emergency `/forcereturn` still works
- Add `/grantemergencyaccess` command for urgent situations

---

## 🧪 Testing Strategy

### Test Cases

**Access Control:**
- [ ] User with correct role can sign out
- [ ] User without role is denied
- [ ] User with multiple roles (any match grants access)
- [ ] Admin bypass works
- [ ] Open access tools ignore roles

**Role Management:**
- [ ] Add role requirement
- [ ] Remove role requirement
- [ ] Remove all requirements (open access)
- [ ] List roles for specific tool
- [ ] List all restricted tools

**Edge Cases:**
- [ ] Role deleted from Discord (handle gracefully)
- [ ] User loses role while signed out (allow completion)
- [ ] Tool deleted while role requirements exist (cascade delete)
- [ ] Multiple admins changing requirements simultaneously

---

## 📈 Rollout Plan

### Phase 4A: Infrastructure (Week 1)
1. Create database tables and migrations
2. Implement permission checking module
3. Add role repository
4. Unit tests for permission logic

### Phase 4B: Commands (Week 2)
5. Implement admin commands (requirerole, removerole, listroles)
6. Update /signout with permission check
7. Add user commands (myaccess)
8. Integration tests

### Phase 4C: Pilot (Week 3)
9. Deploy to test server
10. Configure 2-3 tools with role requirements
11. Test with real users
12. Gather feedback

### Phase 4D: Production (Week 4)
13. Document role structure
14. Train admins on new commands
15. Deploy to production
16. Configure all restricted tools
17. Announce to users

---

## 📚 Documentation Needs

- [ ] Admin guide: How to set up role requirements
- [ ] User guide: Understanding tool access levels
- [ ] Training checklist for role assignment
- [ ] Role naming conventions
- [ ] Emergency procedures

---

## 🎯 Success Metrics

| Metric | Target |
|--------|--------|
| Access denial rate | <5% of attempts |
| False positives | 0% |
| Admin setup time per tool | <2 minutes |
| User confusion rate | <10% |
| Permission check latency | <100ms |

---

## 💰 Benefits

### Safety
 Prevents untrained users from accessing dangerous equipment  
 Reduces accidents and injuries  
 Legal liability protection

### Accountability
 Clear audit trail of who can access what  
 Training requirements enforced automatically  
 Easy to revoke access if needed

### Organization
 Structured onboarding for new members  
 Progressive skill-level system  
 Encourages training participation

### Flexibility
 Easy to add/remove requirements  
 Granular control per tool  
 Role hierarchy support

---

## 🔮 Future Enhancements

- **Temporary Access:** Grant role for X days (training period)
- **Prerequisite Roles:** "Must have Basic before Advanced"
- **Certification Expiry:** Roles expire after 6 months (require recertification)
- **Training Integration:** Auto-grant role after completing training bot quiz
- **Usage Quotas:** "Can only use tool 3 times per week"
- **Time-Based Access:** "Can only use tool during staffed hours"

---

##  Next Steps

1. Review and approve this design
2. Create database migration script
3. Implement `permissions.py` module
4. Add admin commands
5. Test with pilot group
6. Document procedures
7. Roll out to production
