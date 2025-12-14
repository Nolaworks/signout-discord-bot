# NOLAWorks Community Discord Tool Signout Bot

A Discord bot for managing tool reservations through dedicated signout channels. Users can reserve tools using natural language time inputs, and the system tracks all reservations in a PostgreSQL database.

## Table of Contents

- [Overview](#overview)
- [User Guide](#user-guide)
- [Administrator Guide](#administrator-guide)
- [Installation & Deployment](#installation--deployment)
- [Configuration](#configuration)

---

## Overview

The bot enables members to reserve tools via slash commands in dedicated signout channels. It uses OpenAI's API to parse natural language time inputs like "now to 5pm" or "tomorrow 2pm-4pm" into exact time ranges.

### Key Features

- **Natural Language Time Parsing** - Type times naturally like "tomorrow afternoon" or "Friday 2-4pm"
- **Conflict Prevention** - Automatically detects and prevents overlapping reservations
- **Photo Requirements** - Tool room channels require photos for accountability
- **Role-Based Access** - Control which members can reserve specific tools
- **Re-Signout Limits** - Prevent tool monopolization with configurable cooldown periods
- **Admin Blocks** - Block tools for maintenance or events
- **Automatic Notifications** - Get reminders before reservations start and end
- **Waitlist System** - Join waitlists to be notified when tools become available
- **Complete History** - All reservations tracked in database for reporting

---

## User Guide

All commands must be used in the appropriate tool's signout channel.

### Making a Reservation

**`/signout time:<time> [photo:<image>]`**

Reserve a tool for a specific time period. In tool room channels, a photo is required.

Examples:
- `/signout time:now for 2 hours`
- `/signout time:3pm to 5pm`
- `/signout time:tomorrow 10am-12pm`
- `/signout time:Friday afternoon`

### Returning a Tool

**`/returntool reservation:<select> [photo:<image>]`**

Mark your reservation as complete. Select from your active reservations. Tool room channels require a return photo.

### Canceling a Reservation

**`/cancel reservation:<select>`**

Cancel a reservation you no longer need. Select from your active reservations.

### Other User Commands

| Command | Description |
|---------|-------------|
| `/reservations` | View all active reservations for the current tool |
| `/adjusttime` | Modify the start or end time of your reservation |
| `/comment comment:<text>` | Post a comment about the tool |
| `/notifyprefs` | Configure notification settings |
| `/waitlist action:<add\|remove>` | Join or leave the waitlist |
| `/mywaitlist` | View tools you're waitlisted for |
| `/help` | Display user command reference |

### Notifications

The bot sends automatic notifications:
- 15 minutes before your reservation starts
- 15 minutes before your reservation ends
- When a waitlisted tool becomes available

Configure preferences with `/notifyprefs`.

---

## Administrator Guide

Admin commands require Administrator permission in Discord.

### Command Groups

| Group | Description |
|-------|-------------|
| `/admin tool` | Add, remove, and configure tools |
| `/admin reservation` | Manage user reservations |
| `/admin block` | Block tools for maintenance |
| `/admin limit` | Configure re-signout limits |
| `/admin exempt` | Manage limit exemptions |
| `/admin role` | Control role-based access |
| `/debug logs` | View and configure logging |

### Tool Management

```
/admin tool add name:<tool> [max_hours:<int>] [role_required:<bool>]
```
Add a new tool. Role requirement enabled by default.

```
/admin tool remove name:<tool>
```
Remove a tool and all associated data.

```
/admin tool maxtime hours:<int>
```
Set maximum reservation time for the current tool.

### Reservation Management

| Command | Description |
|---------|-------------|
| `/admin reservation clear` | Clear all reservations for current tool |
| `/admin reservation forcereturn` | Force return active reservation |
| `/admin reservation adjust` | Modify another user's reservation |

### Admin Blocks

```
/admin block add tool:<select> time:<range> [force:<bool>]
```
Block a tool from reservations. Select "[All Tools]" to block everything. Use `force:true` to override existing reservations.

```
/admin block remove block:<select>
```
Remove an active admin block.

```
/admin block list
```
View all active blocks.

### Re-Signout Limits

Prevent tool monopolization by limiting consecutive reservations.

```
/admin limit set max:<int> cooldown:<hours> [reset_after:<hours>] [min_total:<hours>]
```
- `max` - Maximum consecutive signouts before cooldown
- `cooldown` - Hours user must wait before reserving again
- `reset_after` - Hours of inactivity before counter resets (default: 24)
- `min_total` - Minimum accumulated hours before reset applies (default: 48)

| Command | Description |
|---------|-------------|
| `/admin limit view` | View all tool limits |
| `/admin limit check` | See users in cooldown |
| `/admin limit clear user:<name>` | Reset user's cooldown |

### Exemptions

| Command | Description |
|---------|-------------|
| `/admin exempt add user:<name> [expires:<date>]` | Exempt user from limits |
| `/admin exempt remove user:<name>` | Remove exemption |
| `/admin exempt list` | View all exemptions |

### Role Management

Control access to tools with Discord roles.

| Command | Description |
|---------|-------------|
| `/admin role toggle` | Enable/disable role requirement |
| `/admin role assign user:<name>` | Grant tool access |
| `/admin role revoke user:<name>` | Remove tool access |
| `/admin role sync` | Create roles for all tools |

### Logging & Debugging

| Command | Description |
|---------|-------------|
| `/debug logs level level:<level>` | Set logging level |
| `/debug logs tail [lines:<int>]` | View recent logs |
| `/debug logs watch enable:<bool>` | Stream logs to channel |
| `/testnotify` | Test notification system |
| `/adminsummary` | Send reservation summary |
| `/adminhelp` | Admin command reference |

---

## Installation & Deployment

### Requirements

- Python 3.12+
- PostgreSQL 14+
- Discord bot token
- OpenAI API key

### Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/Nolaworks/signout-discord-bot.git
   cd signout-discord-bot
   ```

2. **Create virtual environment**
   ```bash
   python3 -m venv .bot-venv
   source .bot-venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. **Initialize database**
   ```bash
   python3 helper_scripts/migrate_to_db.py
   ```

5. **Start the bot**
   ```bash
   python3 mainbot.py
   ```

### Migrating from JSON/CSV System

If you have existing data from a previous JSON/CSV-based system:

1. **Copy your data files**
   ```bash
   mkdir -p archive
   cp /path/to/old/tools.json archive/
   cp /path/to/old/history.csv archive/
   ```

2. **Run migration**
   ```bash
   # Fresh migration (clears database first)
   python3 helper_scripts/migrate_to_db.py --reset --path archive/
   
   # Add to existing data
   python3 helper_scripts/migrate_to_db.py --path archive/
   ```

3. **Sync statistics**
   ```bash
   python3 scripts/sync_user_statistics.py --apply
   ```

#### Migration Options

| Option | Description |
|--------|-------------|
| `--path <dir>` | Directory containing tools.json and history.csv |
| `--archive` | Use archive/ directory (auto-detected if exists) |
| `--reset` | Clear all database tables before migration |
| `--help` | Show help message |

#### User ID Mapping

Migrated users receive temporary IDs (`migrated_username`). When a user makes their first reservation after migration, their account is automatically linked to their real Discord ID, preserving all historical data.

### Systemd Service

For production deployment, use systemd:

```bash
# Copy service file
sudo cp systemd/signout.service /etc/systemd/system/

# Enable and start
sudo systemctl enable signout.service
sudo systemctl start signout.service

# View logs
sudo journalctl -u signout.service -f
```

---

## Configuration

### Environment Variables

Create a `.env` file with:

```env
# Required
DISCORD_TOKEN=your_discord_bot_token
OPENAI_API_KEY=your_openai_api_key
DATABASE_URL=DATABASE_URL_REDACTEDhost:5432/signout_bot

# Optional
TIMEZONE=America/Chicago
DEFAULT_MAX_TIME_HOURS=168
CLEANUP_INTERVAL_MINUTES=1
LOG_LEVEL=INFO
```

### Database Setup

Create a PostgreSQL database:

```sql
CREATE DATABASE signout_bot;
CREATE USER botuser WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE signout_bot TO botuser;
```

The bot automatically creates all required tables on first run.

---

## Additional Documentation

| Document | Description |
|----------|-------------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Production deployment guide |
| [helper_scripts/helper_README.md](helper_scripts/helper_README.md) | Migration and maintenance scripts |
| [docs/](docs/) | Additional technical documentation |

---

## Support

- Type `/help` for user commands
- Type `/adminhelp` for admin commands
- Contact an administrator for access issues
