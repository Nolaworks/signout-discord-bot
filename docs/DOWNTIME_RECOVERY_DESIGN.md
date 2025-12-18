# Downtime Recovery System Design

## Overview

This document outlines the design for a downtime recovery system that detects when the bot has been offline and scans for potential missed reservation attempts during the downtime period.

## Problem Statement

When the bot is offline, Discord slash commands fail completely with no queuing mechanism. Users attempting to create reservations during downtime are unable to do so, leading to frustration and lost reservations. The bot needs a way to:

1. Detect when it was offline
2. Identify potential reservation attempts during downtime
3. Notify admins for manual recovery
4. Provide transparency to users about the outage

## Solution: Message Scanning on Reconnection

### Approach

On bot startup, detect if there was significant downtime (> 5 minutes). If so, scan signout channel message history for patterns indicating reservation attempts. Generate a report for admins to manually review and process using `/admin signout`.

### Key Principles

- **Human verification required** - Never auto-create reservations
- **Safe by default** - False positives acceptable, false negatives not
- **Use existing infrastructure** - Discord message history API only
- **Leverage existing commands** - Use `/admin signout` for processing
- **Transparent communication** - Notify users about outage

---

## Database Schema

### New Table: `bot_uptime_log`

Tracks bot online/offline periods.

```sql
CREATE TABLE bot_uptime_log (
    id SERIAL PRIMARY KEY,
    went_online TIMESTAMP NOT NULL,
    went_offline TIMESTAMP,
    downtime_minutes INTEGER,
    recovery_scan_completed BOOLEAN DEFAULT FALSE,
    messages_scanned INTEGER DEFAULT 0,
    potential_reservations_found INTEGER DEFAULT 0,
    admin_notified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Fields:**
- `went_online` - Bot startup timestamp
- `went_offline` - Bot shutdown timestamp (NULL if currently online)
- `downtime_minutes` - Calculated duration of downtime
- `recovery_scan_completed` - Whether recovery scan has run
- `messages_scanned` - Count of messages reviewed during scan
- `potential_reservations_found` - Count of flagged messages
- `admin_notified` - Whether admins have been DM'd

### New Table: `missed_reservation_attempts`

Stores individual missed reservation attempts detected during recovery.

```sql
CREATE TABLE missed_reservation_attempts (
    id SERIAL PRIMARY KEY,
    uptime_log_id INTEGER REFERENCES bot_uptime_log(id),
    user_id VARCHAR(255) NOT NULL,
    username VARCHAR(255) NOT NULL,
    channel_name VARCHAR(255) NOT NULL,
    tool_name VARCHAR(255),
    message_content TEXT NOT NULL,
    message_url TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    extracted_time_info TEXT,
    processed BOOLEAN DEFAULT FALSE,
    processed_by VARCHAR(255),
    processed_at TIMESTAMP,
    created_reservation_id INTEGER REFERENCES reservations(id),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Fields:**
- `uptime_log_id` - Links to specific downtime period
- `user_id`, `username` - Who attempted the reservation
- `channel_name` - Which signout channel
- `tool_name` - Extracted or inferred tool name
- `message_content` - Full message text for context
- `message_url` - Discord jump URL to original message
- `timestamp` - When user posted the message
- `extracted_time_info` - Parsed time information if detected
- `processed` - Whether admin has handled this attempt
- `processed_by` - Which admin processed it
- `created_reservation_id` - If reservation was created, link to it
- `notes` - Admin notes about resolution

---

## Implementation Components

### 1. Uptime Tracking (mainbot.py)

**On Bot Ready:**
```python
@bot.event
async def on_ready():
    logger.info("Bot is ready")
    
    with get_db_session() as session:
        uptime_repo = UptimeRepository(session)
        
        # Check if there's an open uptime log (bot crashed/killed)
        last_log = uptime_repo.get_last_uptime()
        
        if last_log and last_log.went_offline is None:
            # Bot was not gracefully shut down
            now = get_now(CENTRAL_TZ)
            last_online = CENTRAL_TZ.localize(last_log.went_online)
            downtime_delta = now - last_online
            downtime_minutes = int(downtime_delta.total_seconds() / 60)
            
            # Update the log
            uptime_repo.close_uptime_log(
                log_id=last_log.id,
                went_offline=now,
                downtime_minutes=downtime_minutes
            )
            
            # Trigger recovery if downtime > threshold
            if downtime_minutes > config.recovery_mode_threshold_minutes:
                logger.warning(f"Detected {downtime_minutes} min downtime - starting recovery")
                await trigger_recovery_scan(
                    bot=bot,
                    session=session,
                    uptime_log=last_log,
                    downtime_start=last_online,
                    downtime_end=now
                )
        
        # Create new uptime log for this session
        uptime_repo.create_uptime_log(went_online=get_now(CENTRAL_TZ))
        session.commit()
    
    # Continue with normal startup
    await bot.tree.sync()
    logger.info("Commands synced")
```

**On Bot Shutdown:**
```python
@bot.event
async def on_close():
    logger.info("Bot shutting down")
    
    try:
        with get_db_session() as session:
            uptime_repo = UptimeRepository(session)
            
            # Close current uptime log
            last_log = uptime_repo.get_last_uptime()
            if last_log and last_log.went_offline is None:
                uptime_repo.close_uptime_log(
                    log_id=last_log.id,
                    went_offline=get_now(CENTRAL_TZ),
                    downtime_minutes=0  # Graceful shutdown
                )
                session.commit()
    except Exception as e:
        logger.error(f"Error during shutdown logging: {e}")
```

### 2. Recovery Scan Module (recovery.py)

New file containing all recovery logic.

**Main Recovery Function:**
```python
async def trigger_recovery_scan(bot, session, uptime_log, downtime_start, downtime_end):
    """
    Main entry point for recovery scanning.
    
    Args:
        bot: Discord bot instance
        session: Database session
        uptime_log: BotUptimeLog entry for this downtime
        downtime_start: Start of downtime period (aware datetime)
        downtime_end: End of downtime period (aware datetime)
    """
    from repositories import ToolRepository, MissedReservationRepository
    
    logger.info(f"Starting recovery scan for downtime: {downtime_start} to {downtime_end}")
    
    # Get all signout channels
    tool_repo = ToolRepository(session)
    missed_repo = MissedReservationRepository(session)
    tools = tool_repo.get_all()
    
    signout_channels = []
    for tool in tools:
        if tool.channel_id:
            channel = bot.get_channel(int(tool.channel_id))
            if channel:
                signout_channels.append({
                    'channel': channel,
                    'tool_name': tool.name
                })
    
    # Scan each channel
    total_messages = 0
    potential_attempts = []
    
    for channel_info in signout_channels:
        channel = channel_info['channel']
        tool_name = channel_info['tool_name']
        
        try:
            # Fetch message history during downtime
            # Note: Discord API may limit how far back we can go
            messages = []
            async for msg in channel.history(
                after=downtime_start,
                before=downtime_end,
                limit=None
            ):
                messages.append(msg)
                total_messages += 1
            
            # Filter for potential reservation attempts
            for msg in messages:
                if is_potential_reservation_attempt(bot, msg):
                    # Parse and extract information
                    attempt_data = parse_reservation_attempt(msg, tool_name)
                    
                    # Store in database
                    missed_repo.create_attempt(
                        uptime_log_id=uptime_log.id,
                        user_id=str(msg.author.id),
                        username=msg.author.name,
                        channel_name=channel.name,
                        tool_name=tool_name,
                        message_content=msg.content,
                        message_url=msg.jump_url,
                        timestamp=msg.created_at,
                        extracted_time_info=attempt_data.get('time_info')
                    )
                    
                    potential_attempts.append({
                        'user': msg.author,
                        'channel': channel,
                        'tool': tool_name,
                        'message': msg,
                        'extracted': attempt_data
                    })
        
        except discord.Forbidden:
            logger.warning(f"No permission to read {channel.name}")
        except Exception as e:
            logger.error(f"Error scanning {channel.name}: {e}")
    
    # Update uptime log
    from repositories import UptimeRepository
    uptime_repo = UptimeRepository(session)
    uptime_repo.update_scan_stats(
        log_id=uptime_log.id,
        messages_scanned=total_messages,
        potential_found=len(potential_attempts),
        scan_completed=True
    )
    session.commit()
    
    # Generate and send admin report
    await send_admin_report(
        bot=bot,
        session=session,
        uptime_log=uptime_log,
        downtime_start=downtime_start,
        downtime_end=downtime_end,
        attempts=potential_attempts,
        total_scanned=total_messages
    )
    
    # Post public announcements
    await post_recovery_announcements(
        bot=bot,
        channels=signout_channels,
        downtime_minutes=uptime_log.downtime_minutes
    )
    
    logger.info(f"Recovery scan complete: {total_messages} messages, {len(potential_attempts)} potential attempts")
```

**Keyword Detection:**
```python
def is_potential_reservation_attempt(bot, message):
    """
    Determine if a message might be a reservation attempt.
    
    Returns:
        bool: True if message should be flagged for review
    """
    # Skip bot messages
    if message.author.bot:
        return False
    
    content_lower = message.content.lower()
    
    # Pattern 1: Bot is mentioned
    if bot.user.mentioned_in(message):
        return True
    
    # Pattern 2: Reservation keywords with time indicators
    reservation_keywords = ['signout', 'sign out', 'reserve', 'book', 'need']
    time_indicators = [r'\d+\s*(am|pm)', r'tomorrow', r'today', r'(mon|tues|wed|thurs|fri|sat|sun)']
    
    has_keyword = any(kw in content_lower for kw in reservation_keywords)
    has_time = any(re.search(pattern, content_lower) for pattern in time_indicators)
    
    if has_keyword and has_time:
        return True
    
    # Pattern 3: Bot offline complaints
    offline_patterns = [
        r'bot.*offline',
        r'bot.*down',
        r'bot.*not working',
        r'can\'t.*signout',
        r'tried.*reserve',
        r'command.*fail'
    ]
    
    if any(re.search(pattern, content_lower) for pattern in offline_patterns):
        return True
    
    return False
```

**Information Extraction:**
```python
def parse_reservation_attempt(message, tool_name):
    """
    Extract structured information from a potential reservation attempt.
    
    Returns:
        dict: Extracted information (time_info, confidence, etc.)
    """
    content = message.content.lower()
    extracted = {
        'time_info': None,
        'confidence': 'low'
    }
    
    # Try to extract time information
    time_patterns = [
        r'(tomorrow|today|tonight)\s+(\d+)\s*(am|pm)',
        r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(\d+)\s*(am|pm)',
        r'(\d+)\s*(am|pm)\s*to\s*(\d+)\s*(am|pm)',
        r'for\s+(\d+)\s*hours?'
    ]
    
    for pattern in time_patterns:
        match = re.search(pattern, content)
        if match:
            extracted['time_info'] = match.group(0)
            extracted['confidence'] = 'medium'
            break
    
    # Check for explicit tool mention
    if tool_name.lower() in content:
        extracted['confidence'] = 'high'
    
    # Check for bot mention
    if '@' in message.content and 'bot' in content:
        if extracted['confidence'] == 'low':
            extracted['confidence'] = 'medium'
    
    return extracted
```

**Admin Report Generation:**
```python
async def send_admin_report(bot, session, uptime_log, downtime_start, downtime_end, 
                           attempts, total_scanned):
    """Send DM to all admins with recovery report."""
    from repositories import UserRepository
    from time_utils import format_datetime
    
    user_repo = UserRepository(session)
    admins = user_repo.get_all_admins()
    
    if not attempts:
        # No attempts found - short notification
        embed = discord.Embed(
            title="Bot Downtime Detected",
            description=(
                f"Bot was offline but no missed reservation attempts were detected.\n\n"
                f"**Downtime:** {format_datetime(downtime_start)} to {format_datetime(downtime_end)}\n"
                f"**Duration:** {uptime_log.downtime_minutes} minutes\n"
                f"**Messages Scanned:** {total_scanned}"
            ),
            color=discord.Color.blue()
        )
    else:
        # Build detailed report
        embed = discord.Embed(
            title="⚠️ Bot Downtime Recovery Report",
            description=(
                f"The bot was offline and potential reservation attempts were detected.\n\n"
                f"**Downtime:** {format_datetime(downtime_start)} to {format_datetime(downtime_end)}\n"
                f"**Duration:** {uptime_log.downtime_minutes} minutes\n"
                f"**Messages Scanned:** {total_scanned}\n"
                f"**Potential Reservations:** {len(attempts)}"
            ),
            color=discord.Color.orange()
        )
        
        # Group attempts by channel
        from collections import defaultdict
        by_channel = defaultdict(list)
        for attempt in attempts:
            by_channel[attempt['channel'].name].append(attempt)
        
        # Add field for each channel
        for channel_name, channel_attempts in by_channel.items():
            attempts_text = []
            for attempt in channel_attempts[:5]:  # Limit to 5 per channel
                msg = attempt['message']
                user_mention = f"<@{msg.author.id}>"
                time_info = attempt['extracted'].get('time_info', 'unknown')
                attempts_text.append(
                    f"• {user_mention} at {msg.created_at.strftime('%I:%M%p')}\n"
                    f"  Time: `{time_info}`\n"
                    f"  [View Message]({msg.jump_url})"
                )
            
            if len(channel_attempts) > 5:
                attempts_text.append(f"  *+{len(channel_attempts)-5} more...*")
            
            embed.add_field(
                name=f"#{channel_name}",
                value="\n".join(attempts_text),
                inline=False
            )
        
        embed.add_field(
            name="📋 Next Steps",
            value=(
                "1. Click message links to view full context\n"
                "2. Contact users to verify their intent\n"
                "3. Use `/admin signout` to process valid requests\n"
                "4. Check `/admin recovery pending` for tracking"
            ),
            inline=False
        )
        
        embed.set_footer(text="Recovery scan completed")
    
    # Send to all admins
    notified_count = 0
    for admin in admins:
        try:
            user = await bot.fetch_user(int(admin.user_id))
            await user.send(embed=embed)
            notified_count += 1
        except Exception as e:
            logger.error(f"Failed to notify admin {admin.username}: {e}")
    
    # Update database
    from repositories import UptimeRepository
    uptime_repo = UptimeRepository(session)
    uptime_repo.mark_admin_notified(uptime_log.id)
    session.commit()
    
    logger.info(f"Sent recovery report to {notified_count} admins")
```

**Public Announcements:**
```python
async def post_recovery_announcements(bot, channels, downtime_minutes):
    """Post announcement in each signout channel about the downtime."""
    
    announcement = (
        f"**Bot was offline for {downtime_minutes} minutes.**\n\n"
        f"If you tried to make a reservation during this time and it failed, "
        f"please contact an admin or submit your request again.\n\n"
        f"Admins have been notified and will help process any missed reservations."
    )
    
    for channel_info in channels:
        try:
            channel = channel_info['channel']
            await channel.send(announcement)
        except Exception as e:
            logger.error(f"Failed to post announcement in {channel.name}: {e}")
```

### 3. Repository Classes (repositories.py)

**UptimeRepository:**
```python
class UptimeRepository:
    """Manages bot uptime logging."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def create_uptime_log(self, went_online: datetime) -> BotUptimeLog:
        """Create new uptime log entry."""
        log = BotUptimeLog(
            went_online=went_online.replace(tzinfo=None)
        )
        self.session.add(log)
        self.session.flush()
        return log
    
    def get_last_uptime(self) -> Optional[BotUptimeLog]:
        """Get most recent uptime log entry."""
        return self.session.query(BotUptimeLog)\
            .order_by(BotUptimeLog.went_online.desc())\
            .first()
    
    def close_uptime_log(self, log_id: int, went_offline: datetime, 
                        downtime_minutes: int):
        """Close an uptime log entry."""
        log = self.session.query(BotUptimeLog).get(log_id)
        if log:
            log.went_offline = went_offline.replace(tzinfo=None)
            log.downtime_minutes = downtime_minutes
            self.session.flush()
    
    def update_scan_stats(self, log_id: int, messages_scanned: int,
                         potential_found: int, scan_completed: bool):
        """Update recovery scan statistics."""
        log = self.session.query(BotUptimeLog).get(log_id)
        if log:
            log.messages_scanned = messages_scanned
            log.potential_reservations_found = potential_found
            log.recovery_scan_completed = scan_completed
            self.session.flush()
    
    def mark_admin_notified(self, log_id: int):
        """Mark that admins have been notified."""
        log = self.session.query(BotUptimeLog).get(log_id)
        if log:
            log.admin_notified = True
            self.session.flush()
```

**MissedReservationRepository:**
```python
class MissedReservationRepository:
    """Manages missed reservation attempts."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def create_attempt(self, uptime_log_id: int, user_id: str, username: str,
                      channel_name: str, tool_name: str, message_content: str,
                      message_url: str, timestamp: datetime, 
                      extracted_time_info: str = None) -> MissedReservationAttempt:
        """Create a missed reservation attempt record."""
        attempt = MissedReservationAttempt(
            uptime_log_id=uptime_log_id,
            user_id=user_id,
            username=username,
            channel_name=channel_name,
            tool_name=tool_name,
            message_content=message_content,
            message_url=message_url,
            timestamp=timestamp,
            extracted_time_info=extracted_time_info
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt
    
    def get_pending_attempts(self) -> List[MissedReservationAttempt]:
        """Get all unprocessed attempts."""
        return self.session.query(MissedReservationAttempt)\
            .filter_by(processed=False)\
            .order_by(MissedReservationAttempt.timestamp)\
            .all()
    
    def mark_processed(self, attempt_id: int, processed_by: str,
                      reservation_id: int = None, notes: str = None):
        """Mark an attempt as processed."""
        attempt = self.session.query(MissedReservationAttempt).get(attempt_id)
        if attempt:
            attempt.processed = True
            attempt.processed_by = processed_by
            attempt.processed_at = datetime.utcnow()
            attempt.created_reservation_id = reservation_id
            attempt.notes = notes
            self.session.flush()
```

### 4. Database Models (database.py)

```python
class BotUptimeLog(Base):
    """Tracks bot online/offline periods for recovery."""
    __tablename__ = "bot_uptime_log"
    
    id = Column(Integer, primary_key=True)
    went_online = Column(DateTime, nullable=False)
    went_offline = Column(DateTime)
    downtime_minutes = Column(Integer)
    recovery_scan_completed = Column(Boolean, default=False)
    messages_scanned = Column(Integer, default=0)
    potential_reservations_found = Column(Integer, default=0)
    admin_notified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class MissedReservationAttempt(Base):
    """Stores potential missed reservations detected during recovery."""
    __tablename__ = "missed_reservation_attempts"
    
    id = Column(Integer, primary_key=True)
    uptime_log_id = Column(Integer, ForeignKey("bot_uptime_log.id"))
    user_id = Column(String(255), nullable=False)
    username = Column(String(255), nullable=False)
    channel_name = Column(String(255), nullable=False)
    tool_name = Column(String(255))
    message_content = Column(Text, nullable=False)
    message_url = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    extracted_time_info = Column(Text)
    processed = Column(Boolean, default=False)
    processed_by = Column(String(255))
    processed_at = Column(DateTime)
    created_reservation_id = Column(Integer, ForeignKey("reservations.id"))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
```

### 5. Configuration (config.py)

```python
# Recovery mode settings
recovery_mode_threshold_minutes: int = 5  # Min downtime to trigger recovery
recovery_scan_lookback_hours: int = 24  # Max history to scan
enable_recovery_mode: bool = True  # Feature flag
```

### 6. Admin Commands (admin_panel.py)

**View Pending Recovery Items:**
```python
@admin_group.command(name="recovery", description="View pending missed reservation attempts")
@is_admin_check()
async def recovery_pending(self, interaction: discord.Interaction):
    """View all unprocessed missed reservation attempts."""
    
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    with get_db_session() as session:
        from repositories import MissedReservationRepository
        missed_repo = MissedReservationRepository(session)
        
        pending = missed_repo.get_pending_attempts()
        
        if not pending:
            await interaction.followup.send(
                "No pending missed reservation attempts.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="Pending Recovery Items",
            description=f"{len(pending)} unprocessed missed reservation attempts",
            color=discord.Color.orange()
        )
        
        for attempt in pending[:10]:  # Show first 10
            embed.add_field(
                name=f"#{attempt.channel_name} - {attempt.username}",
                value=(
                    f"**Time:** {attempt.timestamp.strftime('%m/%d %I:%M%p')}\n"
                    f"**Detected:** `{attempt.extracted_time_info or 'unknown'}`\n"
                    f"[View Message]({attempt.message_url})"
                ),
                inline=False
            )
        
        if len(pending) > 10:
            embed.set_footer(text=f"+{len(pending)-10} more pending items")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
```

---

## Testing Strategy

### Unit Tests

**Keyword Detection:**
- Test various message formats
- Bot mentions vs. keywords
- Time extraction patterns
- Negative cases (casual mentions)

**Time Parsing:**
- Natural language time formats
- Ambiguous cases
- Invalid formats

### Integration Tests

**Mock Downtime Scenario:**
```python
async def test_downtime_recovery():
    # Setup: Create mock message history
    # Simulate bot going offline and coming back
    # Verify recovery scan triggers
    # Check database entries
    # Verify admin notifications
```

### Manual Testing Procedure

1. **Prepare Test Environment:**
   - Stop bot intentionally
   - Note exact timestamp

2. **Create Test Messages:**
   - Post in various signout channels:
     - "@bot signout laser tomorrow 2pm"
     - "need to reserve welder for friday"
     - "bot is offline, can't sign out"
     - Normal conversation (false positive test)

3. **Restart Bot:**
   - Verify recovery scan triggers
   - Check logs for scan progress

4. **Verify Results:**
   - Admin receives DM report
   - Test messages are flagged
   - Database entries created
   - Public announcements posted

5. **Process Attempts:**
   - Use `/admin signout` to create reservations
   - Verify tracking works

---

## Deployment Checklist

- [ ] Database migration created and tested
- [ ] New models added to database.py
- [ ] Repositories implemented and tested
- [ ] Recovery module created
- [ ] Integration into mainbot.py on_ready/on_close
- [ ] Configuration variables added
- [ ] Admin commands implemented
- [ ] Unit tests written
- [ ] Integration tests written
- [ ] Manual testing completed
- [ ] Documentation updated
- [ ] Rollback plan prepared

---

## Edge Cases and Limitations

### Handled Edge Cases

1. **Multiple restarts during downtime**
   - Check `recovery_scan_completed` flag
   - Only scan once per downtime period

2. **Very long outages**
   - Limit scan to configured lookback hours
   - Discord API may not retain all history

3. **False positives**
   - Include full message context in reports
   - Admin verification required before processing

4. **Bot crashes vs. graceful shutdown**
   - Detect unclosed uptime logs on startup
   - Calculate downtime from last online time

5. **No admins available**
   - Store report in database
   - Persistent via `/admin recovery` command

### Known Limitations

1. **Silent slash command failures**
   - Discord doesn't log failed slash commands
   - Only catches users who posted messages

2. **Message history retention**
   - Discord API may not return very old messages
   - Limited by Discord's retention policies

3. **False negatives**
   - Users who tried slash commands but didn't post messages
   - Users who gave up silently

4. **API rate limits**
   - Scanning many channels with long histories may hit limits
   - Implement backoff and retry logic

5. **Time zone ambiguity**
   - Extracted times may be ambiguous
   - Admins should verify with users

---

## Future Enhancements

### Phase 2 Possibilities

1. **Auto-processing with confidence threshold**
   - High-confidence attempts could auto-create reservations
   - Notify user via DM for confirmation

2. **Machine learning for pattern detection**
   - Train on historical data
   - Improve accuracy over time

3. **Web form fallback**
   - External form for submissions during downtime
   - Integrate with recovery system

4. **Persistent queue system**
   - Separate service to accept requests
   - Bot polls queue on startup

5. **User notification system**
   - DM users when their attempt is detected
   - Ask for confirmation before admin processing

### Monitoring and Metrics

Track over time:
- Average downtime duration
- Downtime frequency
- Recovery scan accuracy (true vs. false positives)
- Admin response time
- User satisfaction

---

## Success Criteria

The downtime recovery system will be considered successful if:

1. **Detection:** Downtime > 5 minutes is detected 100% of the time
2. **Scanning:** Message history is scanned without errors
3. **Reporting:** Admins receive clear, actionable reports
4. **Processing:** Admins can easily process missed attempts
5. **Transparency:** Users are informed about outages
6. **Accuracy:** False positive rate < 30% (acceptable given safety focus)
7. **Performance:** Recovery scan completes within 2 minutes for typical outages

---

## Conclusion

This downtime recovery system provides a safety net for missed reservation attempts during bot outages. By scanning message history for patterns and generating admin reports, it enables manual recovery while maintaining the system's integrity and preventing automatic errors.

The human-in-the-loop approach ensures safety while leveraging the new `/admin signout` command for easy processing. The system is transparent to users and provides admins with the tools they need to maintain service quality even after unexpected downtime.
