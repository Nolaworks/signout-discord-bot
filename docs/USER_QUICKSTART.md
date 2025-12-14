# Tool Signout Bot - Quick Start Guide

Welcome! This bot helps you reserve tools in the makerspace. Here's everything you need to know.

---

## What's New in the Updated Bot

If you used the old signout bot, here are the major improvements:

### **New Features**
- **Waitlist System** - Join a waitlist and get notified when tools become available
- **Smart Notifications** - Get reminders before your reservation starts and ends
- **My Reservations** - View all your active reservations across all tools with `/myreservations`
- **Consecutive Signout Limits** - Fair access to high-demand tools (prevents monopolization)
- **Statistics** - See your usage history with `/mystats`
- **PostgreSQL Database** - Faster, more reliable, with full history tracking

### **Changed Commands**
- **`/adjusttime` is now actually useful** - The old version required you to input the exact original time text, which often didn't work. Now you just select from your active reservations and enter a new time.
  - **Old:** `/adjusttime old_time:<paste exact time> choice:<start|end|range> new_value:<text>` (rarely worked)
  - **New:** `/adjusttime reservation:<select from dropdown> new_time:<text>` (actually works!)
  - Can still cancel by typing "cancel" as the new time

### **Better Experience**
- Photo attachments now work more reliably
- Commands respond faster with database improvements
- Automatic reservation cleanup every minute
- Full reservation history is preserved (not just the last 30 days)

---

## How It Works

1. Go to the **signout channel** for the tool you want (e.g., `#signout-laser-cutter`)
2. Use the `/signout` command to reserve it
3. When you're done, use `/returntool` to check it back in

---

## Reserving a Tool

Type `/signout` in the tool's signout channel and enter your time:

```
/signout time: now for 2 hours
/signout time: 3pm to 5pm
/signout time: tomorrow 10am-12pm
/signout time: Friday afternoon
```

**The bot understands natural language!** Just type times the way you'd say them.

### Tool Room Channels
Some channels require a **photo** when signing out. Just attach an image showing the tool/workspace.

---

## Returning a Tool

When you're finished, use:

```
/returntool
```

Select your reservation from the list. Tool room channels require a return photo.

---

## Canceling a Reservation

Need to cancel? Use:

```
/cancel
```

Select the reservation you want to cancel.

---

## Other Useful Commands

| Command | What it does |
|---------|--------------|
| `/reservations` | See who has the tool reserved (in current tool channel) |
| `/myreservations` | See **all your reservations** across all tools |
| `/adjusttime` | Change your reservation time (select reservation, enter new time) |
| `/cancel` | Cancel a reservation |
| `/waitlist action:add` | Get notified when tool is available |
| `/mywaitlist` | See tools you're waiting for |
| `/notifyprefs` | Set up reminder notifications |
| `/mystats` | View your usage statistics |
| `/help` | Full command list |

---

## Notifications

The bot can send you reminders:
- **15 minutes before** your reservation starts
- **15 minutes before** your reservation ends
- **When a waitlisted tool** becomes available

Set your preferences with `/notifyprefs`.

---

## Tips

- **Be specific with times** - "2pm to 4pm" works better than "a couple hours"
- **Return tools promptly** - Others may be waiting!
- **Use the waitlist** - Get notified instead of checking repeatedly
- **Check all your reservations** - Use `/myreservations` to see everything you have reserved
- **Include photos** when required - It helps track tool condition
- **Don't hog high-demand tools** - Some tools have consecutive signout limits for fairness

---

## Common Time Formats

All of these work:

| What you type | What it means |
|---------------|---------------|
| `now for 2 hours` | Starting now, ending in 2 hours |
| `3pm to 5pm` | Today, 3pm to 5pm |
| `tomorrow 10am-12pm` | Tomorrow, 10am to 12pm |
| `Friday 2pm to 6pm` | This Friday, 2pm to 6pm |
| `12/20 9am to 5pm` | December 20th, 9am to 5pm |
| `next Monday afternoon` | Next Monday, ~12pm to 5pm |

---

## Consecutive Signout Limits

Some high-demand tools limit how many times you can sign out consecutively:

**What it means:**
- If you sign out a tool, return it, and sign it out again immediately, that counts as **consecutive signouts**
- After reaching the tool's limit (e.g., 3 times), you'll need to wait through a cooldown period
- If **someone else** signs out the tool between your reservations, your count resets to zero

**Example:** Laser cutter has a limit of 3 consecutive signouts with 24-hour cooldown
1. Monday 9am: Sign out laser cutter - Count: 1
2. Monday 2pm: Return and sign out again - Count: 2
3. Monday 5pm: Return and sign out again - Count: 3
4. Monday 8pm: Try to sign out - BLOCKED (must wait 24 hours)
5. Tuesday 8pm: Cooldown expires, can sign out again

**Why?** This ensures fair access to popular tools so everyone gets a turn.

**Admins can grant exceptions** if you have a legitimate need (ask in the appropriate channel).

---

## Need Help?

- Type `/help` for the full command list
- Contact an administrator if you have access issues
- See `/mystats` to check your usage history
- Check channel descriptions for tool-specific rules
