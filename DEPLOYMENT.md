# Deployment Guide

Complete guide for deploying the Discord Tool Signout Bot to production.

## Table of Contents

- [Requirements](#requirements)
- [PostgreSQL Setup](#postgresql-setup)
- [Bot Installation](#bot-installation)
- [Data Migration](#data-migration)
- [Systemd Service](#systemd-service)
- [Verification](#verification)
- [Maintenance](#maintenance)
- [Troubleshooting](#troubleshooting)

---

## Requirements

- **Server**: Linux (Ubuntu 22.04+ recommended)
- **Python**: 3.12+
- **PostgreSQL**: 14+
- **Discord Bot Token**: From Discord Developer Portal
- **OpenAI API Key**: For natural language time parsing

---

## PostgreSQL Setup

### Install PostgreSQL

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
```

### Secure the PostgreSQL Installation

#### 1. Set Password for Default `postgres` User

By default, the `postgres` system user has no password. Secure it:

```bash
# Set Linux password for postgres user
sudo passwd postgres

# Set PostgreSQL password for postgres role
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'your_secure_admin_password';"
```

#### 2. Create Application Database and User

```bash
sudo -u postgres psql
```

```sql
-- Create database
CREATE DATABASE signout_bot;

-- Create dedicated user with strong password
-- Use a password generator for production!
CREATE USER botuser WITH PASSWORD 'your_secure_bot_password';

-- Grant privileges (least privilege principle)
GRANT CONNECT ON DATABASE signout_bot TO botuser;

-- Connect to the database to grant table privileges
\c signout_bot

-- Grant schema privileges
GRANT USAGE ON SCHEMA public TO botuser;
GRANT CREATE ON SCHEMA public TO botuser;

-- Grant table privileges (bot will create tables on first run)
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO botuser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO botuser;

\q
```

#### 3. Configure PostgreSQL Authentication

Edit `pg_hba.conf` to require password authentication:

```bash
sudo nano /etc/postgresql/*/main/pg_hba.conf
```

Ensure these lines use `scram-sha-256` (most secure) or `md5`:

```
# IPv4 local connections:
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             0.0.0.0/0               scram-sha-256

# IPv6 local connections:
host    all             all             ::1/128                 scram-sha-256
```

#### 4. Configure PostgreSQL for Remote Access (if needed)

If the database is on a different server:

```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
```

Change:
```
listen_addresses = 'localhost'
```
To:
```
listen_addresses = '*'  # Or specific IP
```

#### 5. Restart PostgreSQL

```bash
sudo systemctl restart postgresql
```

#### 6. Configure Firewall (if using remote access)

```bash
# Only allow specific IPs
sudo ufw allow from 192.168.1.0/24 to any port 5432
```

### Verify Database Connection

```bash
psql -h localhost -U botuser -d signout_bot -W
# Enter password when prompted
```

---

## Bot Installation

### 1. Clone Repository

```bash
cd /opt
sudo mkdir -p signout
sudo chown $USER:$USER signout
cd signout

git clone https://github.com/Nolaworks/signout-discord-bot.git
cd signout-discord-bot
```

### 2. Create Virtual Environment

```bash
python3 -m venv .bot-venv
source .bot-venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
chmod 600 .env  # Restrict permissions
nano .env
```

**Required settings:**

```env
# Discord Bot Token (from Discord Developer Portal)
DISCORD_TOKEN=your_discord_bot_token

# OpenAI API Key
OPENAI_API_KEY=your_openai_api_key

# PostgreSQL Connection
# Format: postgresql://HOST:PORT/DATABASEhost:port/database
# URL-encode special characters in password (@ becomes %40, etc.)
DATABASE_URL=postgresql://HOST:PORT/DATABASElocalhost:5432/signout_bot

# Timezone
TIMEZONE=America/Chicago

# Optional settings
DEFAULT_MAX_TIME_HOURS=168
CLEANUP_INTERVAL_MINUTES=1
LOG_LEVEL=INFO
```

### 4. Initialize Database

```bash
source .bot-venv/bin/activate
python3 helper_scripts/migrate_to_db.py
```

### 5. Test Bot Startup

```bash
python3 mainbot.py
```

Expected output:
```
INFO - Initializing database...
INFO - Database initialized successfully
INFO - Bot ready! Logged in as YourBot#1234
```

Press `Ctrl+C` to stop.

---

## Data Migration

If migrating from an existing JSON/CSV-based system:

### 1. Copy Production Data

```bash
mkdir -p archive
# Copy from production server
scp production:/path/to/tools.json archive/
scp production:/path/to/history.csv archive/
```

### 2. Run Migration

```bash
source .bot-venv/bin/activate

# Fresh migration (recommended)
python3 helper_scripts/migrate_to_db.py --reset --path archive/

# Or add to existing data
python3 helper_scripts/migrate_to_db.py --path archive/
```

### 3. Sync Statistics

```bash
python3 scripts/sync_user_statistics.py --apply
```

### 4. Verify Migration

```bash
python3 -c "
from db_session import get_db_session
from sqlalchemy import text

with get_db_session() as session:
    tools = session.execute(text('SELECT COUNT(*) FROM tools')).scalar()
    users = session.execute(text('SELECT COUNT(*) FROM users')).scalar()
    history = session.execute(text('SELECT COUNT(*) FROM reservation_history')).scalar()
    print(f'Tools: {tools}, Users: {users}, History: {history}')
"
```

---

## Systemd Service

### 1. Create Service File

```bash
sudo nano /etc/systemd/system/signout.service
```

```ini
[Unit]
Description=Discord Tool Signout Bot
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/signout/signout-discord-bot
Environment=PATH=/opt/signout/signout-discord-bot/.bot-venv/bin

# Wait for database to be ready
ExecStartPre=/bin/sh -c 'for i in 1 2 3 4 5; do pg_isready -h localhost -p 5432 && exit 0 || sleep 5; done; exit 1'

ExecStart=/opt/signout/signout-discord-bot/.bot-venv/bin/python3 /opt/signout/signout-discord-bot/mainbot.py

Restart=always
RestartSec=10

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/signout/signout-discord-bot

[Install]
WantedBy=multi-user.target
```

### 2. Enable and Start Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable signout.service
sudo systemctl start signout.service
```

### 3. Check Status

```bash
sudo systemctl status signout.service
sudo journalctl -u signout.service -f
```

---

## Verification

### Test User Commands

1. Go to a `#signout-*` channel
2. Test `/signout time:now for 1 hour`
3. Test `/reservations`
4. Test `/returntool`
5. Test `/cancel`

### Test Admin Commands

1. Test `/admin tool add name:test-tool`
2. Test `/admin limit set max:3 cooldown:24`
3. Test `/admin block add tool:test-tool time:now for 1 hour`
4. Test `/admin block remove`
5. Test `/admin tool remove name:test-tool`

### Verify Database

```bash
sudo -u postgres psql -d signout_bot -c "
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
"
```

---

## Maintenance

### Daily Statistics Sync (Optional)

Create a cron job or systemd timer:

```bash
sudo nano /etc/systemd/system/signout-sync.timer
```

```ini
[Unit]
Description=Sync signout bot statistics daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo nano /etc/systemd/system/signout-sync.service
```

```ini
[Unit]
Description=Sync signout bot statistics

[Service]
Type=oneshot
WorkingDirectory=/opt/signout/signout-discord-bot
ExecStart=/opt/signout/signout-discord-bot/.bot-venv/bin/python3 /opt/signout/signout-discord-bot/scripts/sync_user_statistics.py --apply
```

```bash
sudo systemctl enable signout-sync.timer
sudo systemctl start signout-sync.timer
```

### Database Backups

```bash
# Manual backup
pg_dump -U botuser -h localhost signout_bot > backup_$(date +%Y%m%d).sql

# Automated daily backup (add to crontab)
0 2 * * * pg_dump -U botuser -h localhost signout_bot > /backups/signout_$(date +\%Y\%m\%d).sql
```

### Log Rotation

The bot writes to `bot.log`. Configure logrotate:

```bash
sudo nano /etc/logrotate.d/signout-bot
```

```
/opt/signout/signout-discord-bot/bot.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 644 root root
}
```

### Archive Old History

```sql
-- Archive records older than 1 year
DELETE FROM reservation_history 
WHERE archived_at < NOW() - INTERVAL '1 year';
```

---

## Troubleshooting

### Bot Won't Start

```bash
# Check logs
sudo journalctl -u signout.service -n 100

# Check database connection
psql -h localhost -U botuser -d signout_bot -c "SELECT 1;"

# Verify environment
source .bot-venv/bin/activate
python3 -c "from config import get_config; print(get_config())"
```

### Database Connection Errors

```
OperationalError: could not connect to server
```

- Check PostgreSQL is running: `sudo systemctl status postgresql`
- Verify credentials in `.env`
- Check `pg_hba.conf` authentication method
- Test connection: `psql -h localhost -U botuser -d signout_bot`

### Permission Errors

```
PermissionError: [Errno 13] Permission denied
```

- Check file ownership: `ls -la /opt/signout/signout-discord-bot/`
- Check `.env` permissions: `chmod 600 .env`
- Check log file permissions

### Command Sync Issues

If commands don't appear in Discord:

```bash
# Restart bot to force sync
sudo systemctl restart signout.service

# Check logs for sync errors
sudo journalctl -u signout.service | grep -i sync
```

### Memory Issues

```bash
# Check memory usage
ps aux | grep mainbot

# Add swap if needed
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

---

## Security Checklist

- [ ] PostgreSQL `postgres` user has a strong password
- [ ] Bot database user has minimal required privileges
- [ ] `pg_hba.conf` uses `scram-sha-256` authentication
- [ ] `.env` file has restricted permissions (600)
- [ ] Firewall configured if using remote database
- [ ] Bot token and API keys are not in version control
- [ ] Regular database backups configured
- [ ] Log rotation configured

---

## Quick Reference

| Task | Command |
|------|---------|
| Start bot | `sudo systemctl start signout.service` |
| Stop bot | `sudo systemctl stop signout.service` |
| Restart bot | `sudo systemctl restart signout.service` |
| View logs | `sudo journalctl -u signout.service -f` |
| Check status | `sudo systemctl status signout.service` |
| Manual backup | `pg_dump -U botuser signout_bot > backup.sql` |
| Sync stats | `python3 scripts/sync_user_statistics.py --apply` |
