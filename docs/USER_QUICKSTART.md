# Tool Signout Bot - Quick Start Guide

Welcome! This bot helps you reserve tools in the makerspace. Here's everything you need to know.

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
| `/reservations` | See who has the tool reserved |
| `/adjusttime` | Change your reservation time |
| `/waitlist action:add` | Get notified when tool is available |
| `/mywaitlist` | See tools you're waiting for |
| `/notifyprefs` | Set up reminder notifications |
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

✅ **Be specific with times** - "2pm to 4pm" works better than "a couple hours"

✅ **Return tools promptly** - Others may be waiting!

✅ **Use the waitlist** - Get notified instead of checking repeatedly

✅ **Include photos** when required - It helps track tool condition

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

## Need Help?

- Type `/help` for the full command list
- Contact an administrator if you have access issues
- Check channel descriptions for tool-specific rules

Happy making! 🛠️
