# Phase 2: Enhanced Features - Implementation Plan

**Date:** November 24, 2025  
**Status:** 🚧 PLANNING  
**Estimated Timeline:** 2-3 weeks

---

## Overview

Phase 2 focuses on user experience improvements and feature enhancements that provide immediate value without requiring major infrastructure changes.

---

## 📋 Feature Breakdown

### 1. Notification System 

**Priority:** HIGH  
**Estimated Time:** 1 week  
**Dependencies:** Database (✅), Discord DMs

#### Features

**A. Pre-Reservation Reminders**
- DM user 15 minutes before reservation starts
- Include: tool name, start time, duration
- Allow "Cancel" button in DM

**B. Expiration Warnings**
- DM user 15 minutes before reservation expires
- Include: tool name, time remaining
- Allow "Extend" button (if no conflicts)

**C. Tool Available Notifications (Waitlist)**
- Users can "watch" tools that are currently reserved
- Notify when tool becomes available
- First-come-first-served from waitlist

**D. Admin Notifications**
- Notify admins when user exceeds time limit
- Daily summary of tool usage

#### Implementation

**Files to Create:**
- `notifications.py` - Notification manager
- `waitlist.py` - Waitlist tracking

**Database Changes:**
```sql
-- Add to database.py
CREATE TABLE notification_preferences (
    user_id VARCHAR PRIMARY KEY,
    reminder_enabled BOOLEAN DEFAULT TRUE,
    expiration_warning_enabled BOOLEAN DEFAULT TRUE,
    waitlist_alerts_enabled BOOLEAN DEFAULT TRUE,
    reminder_minutes_before INTEGER DEFAULT 15
);

CREATE TABLE waitlist (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    tool_id INTEGER NOT NULL,
    created_at TIMESTAMP,
    notified_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (tool_id) REFERENCES tools(id)
);
```

**Background Task:**
```python
@tasks.loop(minutes=1)
async def notification_task():
    # Check for upcoming reservations (15 min warning)
    # Check for expiring reservations (15 min warning)
    # Check waitlist for available tools
```

---

### 2. Analytics Dashboard 📊

**Priority:** MEDIUM  
**Estimated Time:** 4 days  
**Dependencies:** Database (✅), Statistics tables (✅)

#### Features

**A. User Commands**
- `/mystats` - Personal statistics
  - Total reservations
  - Total time used
  - Most used tool
  - Average reservation duration
  - On-time return rate

**B. Admin Commands**
- `/toolstats [tool]` - Tool-specific statistics
  - Total reservations
  - Most frequent user
  - Average duration
  - Peak usage hours
  - Utilization percentage

- `/serverstats` - Server-wide statistics
  - Most popular tools
  - Total active users
  - Average reservation length
  - Peak usage times (heatmap)
  - Busiest day of week

**C. Visualization**
- Generate charts using matplotlib
- Post as Discord image embeds
- Weekly automated reports

#### Implementation

**Files to Create:**
- `analytics.py` - Analytics calculation and queries
- `visualizations.py` - Chart generation

**Example Command:**
```python
@bot.tree.command(name="mystats")
async def my_stats(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    stats = analytics.get_user_stats(user_id)
    
    embed = discord.Embed(title=f"📊 Stats for {interaction.user.name}")
    embed.add_field(name="Total Reservations", value=stats.total_reservations)
    embed.add_field(name="Total Time", value=f"{stats.total_hours:.1f} hours")
    embed.add_field(name="Most Used Tool", value=stats.most_used_tool)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
```

---

### 3. Photo Verification Improvements 📸

**Priority:** LOW  
**Estimated Time:** 3 days  
**Dependencies:** External libraries (PIL, pytesseract)

#### Features

**A. Image Quality Checks**
- Minimum resolution (640x480)
- Maximum file size (10MB)
- Brightness/blur detection

**B. Before/After Photos**
- Require photo on signout AND return
- Compare photos to detect damage
- Store both URLs in database

**C. OCR for Tool Tags** (Optional - requires setup)
- Extract tool ID from photo
- Auto-verify correct tool
- Requires pytesseract + tesseract-ocr

#### Implementation

**Files to Modify:**
- `validation.py` - Add image quality checks
- `discord_utils.py` - Photo download and analysis

**Database Changes:**
```sql
-- Modify reservations table
ALTER TABLE reservations ADD COLUMN photo_url_signout TEXT;
ALTER TABLE reservations ADD COLUMN photo_url_return TEXT;
ALTER TABLE reservations ADD COLUMN photo_quality_score INTEGER;
```

**Dependencies:**
```bash
pip install Pillow
# For OCR (optional):
apt-get install tesseract-ocr
pip install pytesseract
```

---

### 4. Advanced Scheduling 📅

**Priority:** MEDIUM-HIGH  
**Estimated Time:** 5 days  
**Dependencies:** Database (✅), Time parsing (✅)

#### Features

**A. Recurring Reservations**
- `/signout_recurring` command
- Options: daily, weekly, monthly
- Auto-creates reservations for next N occurrences
- Skips conflicts automatically

**B. Calendar View**
- `/calendar [tool]` - Visual calendar for next 7 days
- Shows all reservations as Discord embed
- Color-coded by status (active, upcoming, expired)

**C. Bulk Operations**
- `/signout_multiple` - Reserve multiple tools at once
- `/extend_all` - Extend all your active reservations

#### Implementation

**Files to Create:**
- `scheduling.py` - Recurring reservation logic
- `calendar_view.py` - Calendar generation

**Database Changes:**
```sql
CREATE TABLE recurring_reservations (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    tool_id INTEGER NOT NULL,
    start_time TIME NOT NULL,
    duration_hours INTEGER NOT NULL,
    frequency VARCHAR(10) NOT NULL, -- 'daily', 'weekly', 'monthly'
    end_date DATE,
    last_created_date DATE,
    active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (tool_id) REFERENCES tools(id)
);
```

**Example Calendar Output:**
```
📅 Calendar for Tool: CNC Router (Next 7 Days)

Mon 11/25
├─ 09:00-12:00 ✅ @mmennelle
└─ 14:00-17:00 🔄 @benner81

Tue 11/26
├─ 10:00-11:00 🔄 @r.candy
└─ 13:00-18:00 ⏰ Available

Wed 11/27
└─ All day available

Thu 11/28 (Thanksgiving)
└─ 🚫 Admin Block
```

---

## 🎯 Implementation Priority

### Week 1: Notifications (High Impact)
- Day 1-2: Database schema + notification preferences
- Day 3-4: Reminder & expiration warnings
- Day 5: Waitlist system

### Week 2: Analytics + Scheduling
- Day 1-2: User and admin stats commands
- Day 3-4: Calendar view and bulk operations
- Day 5: Recurring reservations

### Week 3: Polish + Photo Improvements
- Day 1-2: Image quality validation
- Day 3: Before/after photo tracking
- Day 4-5: Testing, documentation, bug fixes

---

## 📦 Dependencies to Install

```bash
# For analytics visualization
pip install matplotlib seaborn

# For image processing
pip install Pillow

# For OCR (optional)
apt-get install tesseract-ocr
pip install pytesseract
```

---

## 🧪 Testing Strategy

### Notification System
- [ ] Test DM delivery
- [ ] Test reminder timing accuracy
- [ ] Test waitlist notifications
- [ ] Test notification preferences

### Analytics
- [ ] Verify calculation accuracy
- [ ] Test with empty data
- [ ] Test chart generation
- [ ] Performance test with large datasets

### Scheduling
- [ ] Test recurring reservation creation
- [ ] Test conflict detection
- [ ] Test calendar rendering
- [ ] Test timezone handling

### Photo Verification
- [ ] Test with various image formats
- [ ] Test file size limits
- [ ] Test quality detection
- [ ] Test OCR accuracy

---

## 📊 Success Metrics

| Feature | Metric | Target |
|---------|--------|--------|
| Notifications | DM delivery rate | >95% |
| Waitlist | Time to notify | <5 minutes |
| Analytics | Query response time | <2 seconds |
| Calendar | Render time | <3 seconds |
| Photo Quality | False positive rate | <5% |
| Recurring | Conflict rate | <10% |

---

## 🚀 Rollout Plan

### Phase 2A: Core Features (Week 1-2)
1. Deploy notifications to test server
2. Add analytics commands
3. Monitor performance and usage

### Phase 2B: Advanced Features (Week 3)
4. Add scheduling features
5. Implement photo improvements
6. User acceptance testing

### Phase 2C: Production (Week 4)
7. Deploy to production
8. Monitor errors and performance
9. Gather user feedback
10. Iterate based on feedback

---

##  Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| DM delivery fails | Medium | High | Fallback to channel mentions |
| Notification spam | High | Medium | Rate limiting + preferences |
| Chart generation slow | Low | Low | Cache results, optimize queries |
| Photo storage costs | Low | Low | Compression, size limits |
| Recurring conflicts | Medium | Medium | Preview conflicts before creating |

---

## 📝 Documentation Needs

- [ ] User guide for notification preferences
- [ ] Admin guide for analytics interpretation
- [ ] Tutorial for recurring reservations
- [ ] Photo requirements documentation
- [ ] API documentation for analytics queries

---

## Next Steps

1. ✅ Review and approve Phase 2 plan
2. Create database migration for new tables
3. Implement notifications module (Week 1)
4. Begin analytics module (Week 2)
5. Weekly check-ins for progress review
