# Automated Backup Setup Instructions

## Backup Script Location
`/root/signout-discord-bot/backup_db.sh`

## Manual Backup
To run a backup manually:
```bash
/root/signout-discord-bot/backup_db.sh
```

## Automated Daily Backups

### Setup Cron Job
Add to crontab to run daily at 2 AM:
```bash
# Edit crontab
crontab -e

# Add this line:
0 2 * * * /root/signout-discord-bot/backup_db.sh

# Verify cron job
crontab -l
```

### Alternative: Setup with systemd timer
Create `/etc/systemd/system/signout-backup.service`:
```ini
[Unit]
Description=Signout Bot Database Backup
After=postgresql.service

[Service]
Type=oneshot
User=root
ExecStart=/root/signout-discord-bot/backup_db.sh
```

Create `/etc/systemd/system/signout-backup.timer`:
```ini
[Unit]
Description=Daily Signout Bot Backup

[Timer]
OnCalendar=daily
OnCalendar=02:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable the timer:
```bash
systemctl daemon-reload
systemctl enable signout-backup.timer
systemctl start signout-backup.timer
systemctl status signout-backup.timer
```

## Backup Location
Backups are stored in: `/root/backups/signout_bot/`

## Retention Policy
- Backups are kept for 30 days
- Older backups are automatically deleted
- Backups are compressed with gzip

## Restore from Backup
```bash
# List available backups
ls -lh /root/backups/signout_bot/

# Restore from a backup (example)
gunzip -c /root/backups/signout_bot/signout_bot_20251124_020000.sql.gz | psql -U botuser signout_bot
```

## Monitoring
Check backup logs:
```bash
tail -f /root/backups/signout_bot/backup.log
```

## Quick Setup Command
```bash
# Add cron job for daily 2 AM backups
(crontab -l 2>/dev/null; echo "0 2 * * * /root/signout-discord-bot/backup_db.sh") | crontab -
```
