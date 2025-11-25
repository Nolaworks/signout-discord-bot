# Role-Based Permissions Deployment Checklist

## Pre-Deployment

### 1. Backup Current System
```bash
# Backup database
cd /root/signout-discord-bot
./helper_scripts/backup_db.sh

# Backup code (if not using git)
cd /root
tar -czf signout-bot-backup-$(date +%Y%m%d-%H%M%S).tar.gz signout-discord-bot/
```

### 2. Test Environment Setup (Optional but Recommended)
- [ ] Set up test Discord server
- [ ] Configure test database
- [ ] Test migration script in test environment
- [ ] Test all new commands in test server

### 3. Review Changes
- [ ] Read `ROLE_BASED_PERMISSIONS.md`
- [ ] Review `ROLE_PERMISSIONS_SUMMARY.md`
- [ ] Understand new admin commands
- [ ] Plan which tools will require roles

## Deployment Steps

### 1. Stop the Bot
```bash
# If using systemd
sudo systemctl stop signout-bot

# Or if running manually
# Kill the bot process
```

### 2. Pull/Update Code
```bash
cd /root/signout-discord-bot

# If using git
git pull

# Or manually copy updated files:
# - database.py
# - repositories.py
# - discord_utils.py
# - mainbot.py
# - admin_panel.py
# - helper_scripts/migrate_role_permissions.py
# - ROLE_BASED_PERMISSIONS.md
# - ROLE_PERMISSIONS_SUMMARY.md
```

### 3. Run Database Migration
```bash
cd /root/signout-discord-bot
source .bot-venv/bin/activate
python3 helper_scripts/migrate_role_permissions.py
```

**Expected output:**
```
============================================================
Role-Based Permissions Migration
============================================================

Initializing database connection...
✓ Database connection initialized
Starting role columns migration...
✓ Added 'role_id' column
✓ Added 'role_required' column

Verifying migration...
Total tools in database: X

 Migration completed successfully!
```

### 4. Verify Database Changes
```bash
# Connect to database and check
psql -U botuser -d signout_bot

# Run these queries:
\d tools

# Should show:
# role_id       | character varying(50) | 
# role_required | boolean               | default false

# Check existing tools
SELECT name, role_id, role_required FROM tools LIMIT 5;

# Exit
\q
```

### 5. Start the Bot
```bash
# If using systemd
sudo systemctl start signout-bot
sudo systemctl status signout-bot

# Or start manually
cd /root/signout-discord-bot
source .bot-venv/bin/activate
python3 mainbot.py
```

### 6. Verify Bot Started Successfully
```bash
# Check logs
tail -f signout.log

# Look for:
# - "Database initialized successfully"
# - "Loading admin panel..."
# - "Logged in as [bot name]"
# - No error messages about role columns
```

## Post-Deployment Configuration

### 1. Create Roles for Existing Tools
In Discord, run:
```
/syncroles
```

**Expected response:**
- Embed showing created roles for each tool
- "Created (X)" section listing all tools
- Confirmation message

### 2. Verify Role Creation
Check Discord server:
- [ ] Roles created with naming format: "Tool: <tool_name>"
- [ ] Roles appear in server settings
- [ ] Roles are blue colored
- [ ] Roles are below bot's role in hierarchy

### 3. Configure Tool Access (As Needed)

For each restricted tool:

**Enable role requirement:**
```
# Go to tool channel (e.g., #signout-3d-printer)
/togglerole
```

**Assign roles to users:**
```
# In the tool channel
/assignrole user:@Username

# Repeat for all users who should have access
```

### 4. Test Functionality

#### Test 1: Role Enforcement
- [ ] User WITHOUT role attempts signout → Should see error with role mention
- [ ] User WITH role attempts signout → Should succeed
- [ ] Admin attempts signout → Should always succeed

#### Test 2: Role Management
- [ ] `/assignrole` grants access correctly
- [ ] User can sign out after receiving role
- [ ] `/revokerole` removes access correctly
- [ ] User cannot sign out after role removed

#### Test 3: Toggle Role Requirement
- [ ] `/togglerole` enables requirement
- [ ] `/togglerole` again disables requirement
- [ ] When disabled, users without role can sign out

#### Test 4: New Tool Creation
- [ ] Create new tool with `/addtool tool:TestTool`
- [ ] Verify role was created automatically
- [ ] Verify role is disabled by default

## Rollback Plan (If Needed)

### If Issues Occur:

1. **Stop the bot**
   ```bash
   sudo systemctl stop signout-bot
   ```

2. **Restore code**
   ```bash
   cd /root
   # Restore from backup
   tar -xzf signout-bot-backup-TIMESTAMP.tar.gz
   ```

3. **Rollback database** (if migration caused issues)
   ```bash
   # The columns are nullable, so not strictly necessary
   # But if needed:
   psql -U botuser -d signout_bot
   ALTER TABLE tools DROP COLUMN role_id;
   ALTER TABLE tools DROP COLUMN role_required;
   \q
   ```

4. **Restart bot with old code**
   ```bash
   sudo systemctl start signout-bot
   ```

## Monitoring

### First 24 Hours

Monitor for:
- [ ] Any error messages in logs about roles
- [ ] Users reporting permission issues
- [ ] Discord API rate limits (if many roles created)
- [ ] Database performance (should be minimal impact)

### Check Logs
```bash
# Watch live logs
tail -f signout.log

# Search for role-related errors
grep -i "role" signout.log | grep -i "error"

# Check recent role operations
grep -i "role requirement" signout.log | tail -20
```

## Troubleshooting

### Bot Can't Create Roles
**Symptom:** "I don't have permission to assign roles"

**Solution:**
1. Check bot has "Manage Roles" permission
2. Ensure bot's role is above tool roles in hierarchy
3. In Discord: Server Settings → Roles → Move bot role up

### Role Not Found
**Symptom:** "Role not found. It may have been deleted."

**Solution:**
```
/syncroles
```
This recreates any missing roles.

### Users Can't Sign Out
**Symptom:** Users see role requirement error unexpectedly

**Check:**
1. Is role requirement enabled? `/togglerole` to check/disable
2. Does role exist in Discord?
3. Run `/syncroles` to fix role issues

### Migration Failed
**Symptom:** Error during migration script

**Solution:**
1. Check database connectivity
2. Verify botuser has ALTER TABLE permissions
3. Review error message for specific issue
4. Contact admin if needed

## Success Criteria

- [ ] Bot starts without errors
- [ ] All existing functionality works
- [ ] `/syncroles` creates roles for all tools
- [ ] `/togglerole` enables/disables requirements
- [ ] `/assignrole` grants access to users
- [ ] Users with roles can sign out
- [ ] Users without roles see appropriate error
- [ ] Admins always have access
- [ ] New tools automatically get roles created

## Communication

### Notify Users About Changes

**Announcement Template:**
```
📢 **Tool Access System Update**

We've added a new role-based permission system for tool signouts!

**What's New:**
• Each tool now has its own Discord role
• You may need a specific role to sign out certain tools
• Admins can grant/revoke access as needed

**What This Means for You:**
• Most tools remain open to everyone (no change)
• Restricted tools will show which role you need
• Contact an admin if you need access to a tool

**For Admins:**
• New commands: /togglerole, /assignrole, /revokerole, /syncroles
• See ROLE_BASED_PERMISSIONS.md for full documentation

Questions? Ask an admin!
```

## Documentation

Remind admins to read:
- [ ] `ROLE_BASED_PERMISSIONS.md` - Complete feature documentation
- [ ] `ROLE_PERMISSIONS_SUMMARY.md` - Quick reference
- [ ] `helper_scripts/helper_README.md` - Migration script info

## Next Steps

After successful deployment:

1. **Gradual Rollout**
   - Start with one or two restricted tools
   - Monitor user feedback
   - Expand to other tools as needed

2. **Training Admins**
   - Show how to use new commands
   - Explain role hierarchy
   - Practice granting/revoking access

3. **Establish Policies**
   - Which tools require roles?
   - Who can grant access?
   - How to request access?

4. **Monitor Usage**
   - Check logs for blocked signouts
   - Track role assignment requests
   - Adjust policies as needed

## Completion

When everything is working:
- [ ] All checks passed
- [ ] No errors in logs
- [ ] Users can sign out normally
- [ ] Role restrictions working as intended
- [ ] Admins trained on new commands
- [ ] Documentation accessible

**Deployment completed:** _______________ (date/time)

**Deployed by:** _______________

**Issues encountered:** _______________

**Notes:** _______________
