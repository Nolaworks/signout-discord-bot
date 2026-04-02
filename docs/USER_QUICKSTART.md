# Tool Bot Updates Guide \- Starting 12/16\!

This bot helps you reserve tools in the makerspace. Here's everything you need to know.

---

## What's New in the Updated Bot

If you used the old signout bot, here are the major improvements:

### **New Features Overview**

- **Tool Room Control** \- There is a new channel category called "Tool Room," All signout channels under that category require a photo to start and end a reservation. More info below\!  
- **Role-Based Tool Access** \- Most tools now require you to have the corresponding role before you can sign them out. This ensures you've been trained on the tool before using it. Contact a shop leader to get trained and receive access.
- **Waitlist System** \- Join a waitlist and get notified when tools become available  
- **Smart Notifications** \- Get reminders before your reservation starts and ends.  
- **Consecutive Signout Limits** - Fair access to high-demand tools (prevents monopolization)  
- **Improved `/adjusttime` command** - Update a portion of your reservation (start, end, or full range) instead of creating a whole new signout

### **Changed Commands**

- **`/adjusttime` is now actually useful**\!  
  - **Old:** `/adjusttime reservation:<select from dropdown> new_time:<text>`  
  - **New:** `/adjusttime old_time:<choose from list> choice:<start|end|range> new_value:<text>`  
  - Can still cancel by typing "cancel" as the new time

---

## How It Works

1. Go to the **signout channel** for the tool you want (e.g., `#signout-laser-cutter`)  
2. Use the `/signout` command to reserve it  
3. When you're done, use `/returntool` to check it back in

---

## Reserving a Tool

Type `/signout` in the tool's signout channel and enter your time:

/signout time: now for 2 hours

/signout time: 3pm to 5pm

/signout time: tomorrow 10am-12pm

/signout time: Friday afternoon

**The bot understands natural language\!** Just type times the way you'd say them.

---

## Tool Room Photo Requirements

**Tool Room channels require photos** to help track tool condition and usage.

### When Signing Out

**If your reservation starts within 30 minutes:**

- You must attach a photo immediately using the `photo:` parameter  
- Example: `/signout time: now for 2 hours photo: [attach image]`

**If your reservation starts more than 30 minutes away:**

- You can sign out without a photo  
- Send the photo to the bot via DM anytime before your reservation starts  
- You'll get a reminder 15 minutes before start time  
- **Deadline: 10 minutes after your start time** or the reservation will be cancelled

### When Returning

- Use `/returntool` and select your reservation  
- Attach a photo showing the tool/workspace in the returned condition  
- If you forget, you have **30 minutes** to send a photo via DM to the bot  
- After 30 minutes, you'll get a "photo debt" that blocks future Tool Room signouts

### Sending Photos via DM

1. Open a direct message with the bot  
2. Simply send the photo (no command needed)  
3. The bot will ask which reservation the photo is for  
4. Reply with the number shown in the list

**Photo debts:** If you have an outstanding photo debt, you cannot sign out any Tool Room tools until an administrator clears it. Contact an admin with the missing photo to resolve the debt.

---

## Returning a Tool

When you're finished, use:

/returntool

Select your reservation from the list. Tool room channels require a return photo.

---

## Canceling a Reservation

Need to cancel? Use:

/cancel

Select the reservation you want to cancel.

---

## Other Useful Commands

| Command | What it does |
| :---- | :---- |
| `/reservations` | See who has the tool reserved (in current tool channel) |
| `/adjusttime` | Change your reservation time (select reservation, enter new time) |
| `/cancel` | Cancel a reservation |
| `/waitlist action:add` | Get notified when tool is available |
| `/mywaitlist` | See tools you're waiting for |
| `/notifyprefs` | Set up reminder notifications |
| `/help` | Full command list |

---

## Notifications

The bot can send you reminders:

- **APPROX 15 minutes before** your reservation starts  
- **15 minutes before** your reservation ends  
- **When a waitlisted tool** becomes available

Set your preferences with `/notifyprefs`.

---

## Tips

- **Get trained first** \- Most tools require you to have the tool's role before signing out. Contact a shop leader for training.
- **Be specific with times** \- "2pm to 4pm" works better than "a couple hours"  
- **Return tools promptly** - Others may be waiting  
- **Use the waitlist** - Get notified instead of checking repeatedly  
- **Include photos** when required \- It helps track tool condition  
- **Don't hog high-demand tools** \- Some tools have consecutive signout limits for fairness

---

## Common Time Formats

All of these work:

| What you type | What it means |
| :---- | :---- |
| `now for 2 hours` | Starting now, ending in 2 hours |
| `3pm to 5pm` | Today, 3pm to 5pm |
| `tomorrow 10am-12pm` | Tomorrow, 10am to 12pm |
| `Friday 2pm to 6pm` | This Friday, 2pm to 6pm |
| `12/20 9am to 5pm` | December 20th, 9am to 5pm |
| `next Monday afternoon` | Next Monday, \~12pm to 5pm |

---

## Consecutive Signout Limits

Some high-demand tools limit how many times you can sign out consecutively:

**What it means:**

- If you sign out a tool, return it, and sign it out again immediately, that counts as **consecutive signouts**  
- After reaching the tool's limit (e.g., 3 times), you'll need to wait through a cooldown period  
- If **someone else** signs out the tool between your reservations, your count resets to zero

**Example:** Laser cutter has a limit of 3 consecutive signouts with 24-hour cooldown

1. Monday 9am: Sign out laser cutter \- Count: 1  
2. Monday 2pm: Return and sign out again \- Count: 2  
3. Monday 5pm: Return and sign out again \- Count: 3  
4. Monday 8pm: Try to sign out \- BLOCKED (must wait 24 hours)  
5. Tuesday 8pm: Cooldown expires, can sign out again

**Why?** This ensures fair access to popular tools so everyone gets a turn.

**Admins can grant exceptions** if you have a legitimate need (ask in the appropriate channel).

---

## Need Help?

- Type `/help` for the full command list  
- **Need tool access?** Contact a shop leader to get trained and receive the tool role
- Contact a shop leader if you have other access issues  
- See `/mystats` to check your usage history  
- Check channel descriptions for tool-specific rules
