#!/bin/bash
# Production Migration Script
# Run this after copying tools.json and history.csv to migration_data/

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"
source .bot-venv/bin/activate

echo "=============================================="
echo "Production Migration Script"
echo "=============================================="
echo ""

# Check for migration files
if [ ! -f "$SCRIPT_DIR/migration_data/tools.json" ]; then
    echo "ERROR: migration_data/tools.json not found!"
    echo "Please copy tools.json from production to migration/migration_data/"
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/migration_data/history.csv" ]; then
    echo "ERROR: migration_data/history.csv not found!"
    echo "Please copy history.csv from production to migration/migration_data/"
    exit 1
fi

echo "✓ Found migration files"
echo ""

# Confirm before proceeding
read -p "This will RESET the database and migrate production data. Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "Step 1: Resetting database..."
python3 helper_scripts/reset_db.py

echo ""
echo "Step 2: Running main migration..."
python3 scripts/migrate_to_db.py --path "$SCRIPT_DIR/migration_data/"

echo ""
echo "Step 3: Running feature migrations..."
python3 helper_scripts/migrate_role_permissions.py
python3 helper_scripts/migrate_photo_enforcement.py
python3 helper_scripts/migrate_photo_table.py
python3 helper_scripts/migrate_consecutive_signouts.py

echo ""
echo "=============================================="
echo "Migration Complete!"
echo "=============================================="
echo ""
echo "Next steps:"
echo "1. Update .env with production bot token"
echo "2. Create and start production service:"
echo "   sudo cp systemd/test-signout.service /etc/systemd/system/signout-bot.service"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable signout-bot"
echo "   sudo systemctl start signout-bot"
echo "3. Verify bot is running: systemctl status signout-bot"
echo "4. Shut down old production VM"
echo ""
