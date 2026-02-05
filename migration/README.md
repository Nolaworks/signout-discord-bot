# Migration Folder

This folder contains everything needed for production migration.

## Structure

```
migration/
├── migrate.sh          # Main migration script (run this)
├── migration_data/     # Place production files here
│   ├── tools.json      # Copy from production
│   └── history.csv     # Copy from production
└── README.md           # This file
```

## Usage

### 1. Copy Production Files
Copy `tools.json` and `history.csv` from production VM to `migration_data/`

### 2. Update Bot Token
Edit `.env` in the project root to use the production bot token.

### 3. Stop Test Bot
```bash
systemctl stop test-signout
```

### 4. Run Migration
```bash
chmod +x migrate.sh
./migrate.sh
```

This script will:
- Reset the database (clear test data)
- Migrate tools and reservations from flat files
- Run all feature migrations (roles, photos, consecutive signouts)

### 5. Start Production Bot
```bash
sudo cp ../systemd/test-signout.service /etc/systemd/system/signout-bot.service
sudo systemctl daemon-reload
sudo systemctl enable signout-bot
sudo systemctl start signout-bot
```

### 6. Verify & Shutdown Old VM
```bash
systemctl status signout-bot
journalctl -u signout-bot -f
```

Once confirmed working, shut down the old production VM.

## Full Checklist
See [docs/PRODUCTION_MIGRATION_CHECKLIST.md](../docs/PRODUCTION_MIGRATION_CHECKLIST.md) for detailed steps.
