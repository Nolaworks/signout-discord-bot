# NOLAWorks Community Discord Tool Signout Bot

## Description
This bot allows Discord users to manage tool reservations using the familiar signout-[tool] channels. It enables users to reserve tools via slash commands (e.g., `/signout` or `/adjusttime`). The bot also integrates with OpenAI's API for natural language parsing, ensuring time inputs are formatted consistently.

For example, if you type:  
`/signout now to 5pm` at 11:24 AM, the bot will process this input using OpenAI and return a formatted time range:  
`03/25/2025 11:24 - 03/25/2025 17:00`.

---

## Key Features
- **Natural Language Time Parsing**: Use phrases like "tomorrow 2pm-4pm" or "Friday afternoon"
- **Conflict Detection**: Prevents double-booking of tools
- **Photo Requirements**: Tool room signouts require photos for accountability
- **Consecutive Signout Limits**: Configurable limits to prevent monopolization with cooldowns
- **Admin Exemptions**: Bypass limits for specific users with optional expiration
- **Role-Based Permissions**: Fine-grained access control per tool (NEW!)
- **Automatic Notifications**: Reminders before reservations end
- **Flexible Adjustments**: Modify or cancel reservations easily
- **Complete History**: Track all tool usage with PostgreSQL database

---

## Commands (All Users)
- **`/signout`** – Reserve a tool for a specific time. This command only works in the corresponding signout channel for the tool.
- **`/adjusttime`** – Adjust a reservation to a new time or cancel it by typing "cancel" when prompted.  
  - **Admins** can adjust any user's reservation.  
  - **Regular users** can only modify their own.
- **`/reservations`** – View all reservations for a tool.
- **`/returntool`** – Mark a tool as returned if no return time was originally specified.

---

## Admin Commands

### Tool Management
- **`/addtool`** – Add a new tool with auto-created role
- **`/removetool`** – Remove a tool from the system
- **`/listtools`** – View all tools and their settings
- **`/adjustmaxtime`** – Change maximum reservation time for a tool
- **`/adminblock`** – Block a tool for maintenance

### Role-Based Permissions (NEW!)
- **`/syncroles`** – Create Discord roles for all tools
- **`/togglerole`** – Enable/disable role requirement for current tool
- **`/assignrole`** – Give a user access to sign out current tool
- **`/revokerole`** – Remove a user's access to current tool

### Consecutive Signout Management
- **`/setresignoutlimit`** – Set max consecutive re-signouts with cooldown
- **`/viewresignoutlimits`** – View limits for all tools
- **`/clearcooldown`** – Remove cooldown for a user
- **`/checkcooldowns`** – View all active cooldowns
- **`/exemptuser`** – Exempt user from consecutive limits
- **`/removeexemption`** – Remove user exemption
- **`/listexemptions`** – View all exemptions for current tool

### User Management
- **`/listusers`** – View all users in the database
- **`/stats`** – View usage statistics

---

## Role-Based Permissions

Each tool can have its own Discord role for access control. See **[ROLE_QUICK_REFERENCE.md](ROLE_QUICK_REFERENCE.md)** for details.

**Quick Start:**
1. Run `/syncroles` to create roles for all tools
2. In tool channel, run `/togglerole` to enable requirement
3. Use `/assignrole user:@Member` to grant access

**Documentation:**
- **[ROLE_QUICK_REFERENCE.md](ROLE_QUICK_REFERENCE.md)** - Quick command reference
- **[ROLE_BASED_PERMISSIONS.md](ROLE_BASED_PERMISSIONS.md)** - Complete documentation
- **[ROLE_DEPLOYMENT_CHECKLIST.md](ROLE_DEPLOYMENT_CHECKLIST.md)** - Deployment guide

---

## How to Use:
1. Navigate to the **signout channel** of the tool you want to reserve.
2. Use the appropriate command from the list above.
3. **Double-check your reservation for correctness.**

### Additional Notes:
- Signout channels also serve as discussion spaces about the tools.  
- **Initially, signout channels will be restricted to slash commands only** to encourage proper usage.  
- After about a month, general conversations will be re-enabled.
- Some tools may require a specific role - contact an admin if you need access.

---

## Documentation

### User Guides
- **[QUICKSTART.md](QUICKSTART.md)** - Quick setup guide
- **[REFERENCE.md](REFERENCE.md)** - Command reference

### Admin Documentation  
- **[ROLE_QUICK_REFERENCE.md](ROLE_QUICK_REFERENCE.md)** - Role commands quick reference
- **[ROLE_BASED_PERMISSIONS.md](ROLE_BASED_PERMISSIONS.md)** - Complete role system guide
- **[PHASE_4_ROLES_DESIGN.md](PHASE_4_ROLES_DESIGN.md)** - Role system design

### Deployment
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Production deployment guide
- **[ROLE_DEPLOYMENT_CHECKLIST.md](ROLE_DEPLOYMENT_CHECKLIST.md)** - Role feature deployment
- **[helper_scripts/helper_README.md](helper_scripts/helper_README.md)** - Migration scripts

### Development
- **[REFACTORING.md](REFACTORING.md)** - Codebase structure
- **[COMPLETION_SUMMARY.md](COMPLETION_SUMMARY.md)** - Implementation summary
