#!/bin/bash
#
# Automated PostgreSQL Backup Script
# Backs up the signout_bot database and manages retention
#

# Configuration
BACKUP_DIR="/root/backups/signout_bot"
DATABASE_NAME="signout_bot"
DATABASE_USER="botuser"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Log file
LOG_FILE="$BACKUP_DIR/backup.log"

# Function to log messages
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log_message "Starting database backup..."

# Perform backup
BACKUP_FILE="$BACKUP_DIR/${DATABASE_NAME}_${DATE}.sql"
if pg_dump -U "$DATABASE_USER" "$DATABASE_NAME" > "$BACKUP_FILE" 2>> "$LOG_FILE"; then
    log_message "Backup successful: $BACKUP_FILE"
    
    # Compress the backup
    if gzip "$BACKUP_FILE" 2>> "$LOG_FILE"; then
        log_message "Compression successful: ${BACKUP_FILE}.gz"
        BACKUP_FILE="${BACKUP_FILE}.gz"
    else
        log_message "WARNING: Compression failed, keeping uncompressed backup"
    fi
    
    # Get file size
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log_message "Backup size: $SIZE"
else
    log_message "ERROR: Backup failed!"
    exit 1
fi

# Clean up old backups (keep last 30 days)
log_message "Cleaning up backups older than $RETENTION_DAYS days..."
DELETED=$(find "$BACKUP_DIR" -name "${DATABASE_NAME}_*.sql.gz" -mtime +$RETENTION_DAYS -delete -print | wc -l)
log_message "Deleted $DELETED old backup(s)"

# Count remaining backups
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "${DATABASE_NAME}_*.sql.gz" | wc -l)
log_message "Total backups retained: $BACKUP_COUNT"

log_message "Backup process complete"

# Exit successfully
exit 0
