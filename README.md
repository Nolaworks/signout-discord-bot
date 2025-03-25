# NOLAWorks Community Discord Tool Signout Bot

## Description
This bot allows Discord users to manage tool reservations using the familiar signout-[tool] channels. It enables users to reserve tools via slash commands (e.g., `/signout` or `/adjusttime`). The bot also integrates with OpenAI’s API for natural language parsing, ensuring that time inputs are formatted consistently.

For example, if you type:  
`/signout now to 5pm` at 11:24 AM, the bot will process this input using OpenAI and return a formatted time range:  
`03/25/2025 11:24 - 03/25/2025 17:00`.

---

## Commands (All Users)
- **`/signout`** – Reserve a tool for a specific time. This command only works in the corresponding signout channel for the tool.
- **`/adjusttime`** – Adjust a reservation to a new time or cancel it by typing "cancel" when prompted.  
  - **Admins** can adjust any user’s reservation.  
  - **Regular users** can only modify their own.
- **`/reservations`** – View all reservations for a tool.
- **`/returntool`** – Mark a tool as returned if no return time was originally specified.

---

## How to Use:
1. Navigate to the **signout channel** of the tool you want to reserve.
2. Use the appropriate command from the list above.
3. **Double-check your reservation for correctness.**

### Additional Notes:
- Signout channels also serve as discussion spaces about the tools.  
- **Initially, signout channels will be restricted to slash commands only** to encourage proper usage.  
- After about a month, general conversations will be re-enabled.

  
