# NOLAWorks Community Discord Tool Signout Bot

A Discord bot for managing tool reservations through dedicated signout channels. Users can reserve tools using natural language time inputs, and the system tracks all reservations in a PostgreSQL database.

## Overview

The bot enables members to reserve tools via slash commands in dedicated signout channels. It uses OpenAI's API to parse natural language time inputs like "now to 5pm" or "tomorrow 2pm-4pm" into exact time ranges. All commands are slash commands, making them easy to discover and use on mobile devices.

## Key Features

- **Natural Language Time Parsing**: Type times naturally like "tomorrow afternoon" or "Friday 2-4pm"
- **Conflict Prevention**: Automatically detects and prevents overlapping reservations
- **Photo Requirements**: Tool room channels require photos for accountability
- **Role-Based Access**: Control which members can reserve specific tools
- **Re-Signout Limits**: Prevent tool monopolization with configurable cooldown periods
- **Admin Blocks**: Block tools for maintenance or events
- **Automatic Notifications**: Get reminders before reservations start and end
- **Waitlist System**: Join waitlists to be notified when tools become available
- **Complete History**: All reservations tracked in database for reporting and auditing

## User Commands

All commands must be used in the appropriate tool's signout channel.

### Basic Commands

**`/signout time:<time> [photo:<image>]`**
Reserve a tool for a specific time period. In tool room channels, you must attach a photo.
- Examples: "now for 2 hours", "3pm to 5pm", "tomorrow 10am-12pm"

**`/returntool reservation:<pick> [photo:<image>]`**
Mark your reservation as complete. Select from your active reservations. Tool room channels require a return photo.

**`/cancel reservation:<pick>`**
Cancel a reservation you no longer need. Select from your active reservations.

**`/reservations`**
View all active reservations for the current tool.

**`/adjusttime`**
Modify the start time, end time, or both for your existing reservation.

**`/comment comment:<text>`**
Post a comment about the tool visible to all users.

### Notification Commands

**`/notifyprefs`**
Configure your notification settings for reservation reminders.

**`/waitlist action:<add|remove>`**
Join or leave the waitlist for the current tool.

**`/mywaitlist`**
View all tools you're currently waitlisted for.

**`/help`**
Display the complete user command reference.

## Admin Commands

Admin commands are organized into groups for easier navigation. All admin commands require Administrator permission in Discord.

### Command Groups

Type `/admin` or `/debug` to see available command groups:
- **`/admin tool`** - Add, remove, and configure tools
- **`/admin reservation`** - Manage user reservations
- **`/admin block`** - Block tools for maintenance
- **`/admin limit`** - Configure re-signout limits
- **`/admin exempt`** - Manage limit exemptions
- **`/admin role`** - Control role-based access
- **`/debug logs`** - View and configure logging

### Tool Management

**`/admin tool add name:<tool> [max_hours:<int>] [role_required:<bool>]`**
Add a new tool to the system. Role requirement is enabled by default. Tools added in tool room channels automatically require photos.

**`/admin tool remove name:<tool>`**
Remove a tool from the system. All associated reservations and settings are deleted.

**`/admin tool maxtime hours:<int>`**
Set the maximum reservation time for the current tool.

### Reservation Management

**`/admin reservation clear`**
Clear all reservations for the current tool.

**`/admin reservation forcereturn`**
Force return the currently active reservation.

**`/admin reservation adjust`**
Modify another user's reservation time.

### Admin Blocks

**`/admin block add tool:<select> time:<range> [force:<bool>]`**
Block a tool from being reserved. Select "[All Tools]" to block everything. Use force:true to override existing reservations.

**`/admin block remove block:<select>`**
Remove an active admin block. Select from the list of current blocks.

**`/admin block list`**
View all active admin blocks across all tools.

### Re-Signout Limits

**`/admin limit set max:<int> cooldown:<hours>`**
Configure how many times users can consecutively re-sign out the current tool, and the cooldown period before they can reserve it again.

**`/admin limit view`**
View re-signout limits configured for all tools.

**`/admin limit check`**
See which users are currently in cooldown for the current tool.

**`/admin limit clear user:<name>`**
Reset a user's cooldown counter for the current tool.

### Exemptions

**`/admin exempt add user:<name> [expires:<date>]`**
Exempt a user from re-signout limits for the current tool. Optionally set an expiration date.

**`/admin exempt remove user:<name>`**
Remove a user's exemption for the current tool.

**`/admin exempt list`**
View all exemptions configured for the current tool.

### Role Management

**`/admin role toggle`**
Enable or disable role requirement for the current tool.

**`/admin role assign user:<name>`**
Give a user access to sign out the current tool.

**`/admin role revoke user:<name>`**
Remove a user's access to sign out the current tool.

**`/admin role sync`**
Create Discord roles for all tools and synchronize permissions.

### Notifications

**`/testnotify`**
Test the notification system by sending yourself a test notification.

**`/adminsummary`**
Manually send the daily reservation summary to all administrators.

### Logging

**`/debug logs level level:<DEBUG|INFO|WARNING|ERROR>`**
Change the bot's logging level.

**`/debug logs tail [lines:<int>]`**
View recent log entries.

**`/debug logs watch enable:<bool>`**
Enable or disable live log streaming to the current channel.

**`/adminhelp`**
Display the complete admin command reference.

## How to Use

### For Regular Users

1. Navigate to the signout channel for the tool you want to reserve (e.g., #signout-laser-cutter)
2. Type `/signout` and enter your desired time
3. In tool room channels, attach a photo of the tool
4. When finished, use `/returntool` to mark it as returned
5. Use `/cancel` if you need to cancel your reservation early

### For Administrators

1. Use `/admin tool add` to create new tools
2. Configure role requirements with `/admin role toggle`
3. Set re-signout limits with `/admin limit set` to prevent monopolization
4. Use `/admin block add` to block tools for maintenance or events
5. Grant access to individual users with `/admin role assign`

## Photo Requirements

Tool room channels automatically require photos for signouts and returns. This ensures accountability and provides visual documentation of tool condition. Regular channels make photos optional but recommended.

## Role-Based Access

Each tool can have a dedicated Discord role for access control. By default, role requirements are enabled for new tools to provide secure access control. Administrators automatically bypass role requirements.

To manage tool access:
1. Use `/admin role sync` to create roles for all tools
2. Enable role requirement with `/admin role toggle` (if not already enabled)
3. Assign users with `/admin role assign user:@Member`
4. Revoke access with `/admin role revoke user:@Member`

## Re-Signout Limits

Prevent users from monopolizing tools by setting consecutive re-signout limits. When a user reaches their limit, they must wait through a cooldown period before reserving that tool again.

Example: Set limit to 3 re-signouts with 24-hour cooldown
- User reserves tool 4 times in a row
- After 4th return, they cannot reserve again for 24 hours
- Other users can reserve during this cooldown
- Admins are automatically exempt from all limits

## Notifications

The bot sends automatic notifications:
- 15 minutes before reservation starts
- 15 minutes before reservation ends
- When waitlisted tool becomes available

Users can configure their notification preferences with `/notifyprefs`.

## Getting Help

- Type `/help` for user command reference
- Type `/adminhelp` for admin command reference
- Contact an administrator for access issues or questions

## Getting Help

- Type `/help` for user command reference
- Type `/adminhelp` for admin command reference
- Contact an administrator for access issues or questions

## Technical Details

### Requirements
- Python 3.12+
- PostgreSQL database
- Discord bot token
- OpenAI API key

### Installation

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Configure environment variables in `.env` file
4. Run database migrations if needed
5. Start the bot: `python mainbot.py`

### Environment Configuration

Required environment variables:
- `TEST_DISCORD_TOKEN` or `DISCORD_TOKEN` - Discord bot token
- `OPENAI_API_KEY` - OpenAI API key for natural language parsing
- `DATABASE_URL` - PostgreSQL connection string

Optional environment variables:
- `TIMEZONE` - Default: "America/Chicago"
- `DEFAULT_MAX_TIME_HOURS` - Default: 168 (1 week)
- `CLEANUP_INTERVAL_MINUTES` - Default: 1

See `config.py` for complete configuration options.

## Additional Documentation

For administrators and developers, additional documentation is available in the repository:
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Production deployment guide
- **[REFACTORING.md](REFACTORING.md)** - Codebase structure and design
- **[helper_scripts/helper_README.md](helper_scripts/helper_README.md)** - Database migration scripts
