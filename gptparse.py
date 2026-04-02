import datetime
import logging
import os
import pytz
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

async def parse_time_with_gpt(time_str):
    """Uses OpenAI to parse a user-provided time string into MM-DD-YYYY HH:MM or a range MM-DD-YYYY HH:MM to HH:MM."""
    central_tz = pytz.timezone("America/Chicago")
    current_time = datetime.datetime.now(central_tz).strftime("%m-%d-%Y %H:%M")

    prompt = f"""

You are a time expression parser. Convert the following time expression into a standard format:

All times will be either present or future.

- If it's a single time, return: MM-DD-YYYY HH:MM
- If it's a time range, return: MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM

---

Interpretation Rules:

1. Accepted Range Connectors

Interpret the following as equivalent: "to", "until", "til", "till", "untill", and "-".

"3 till 5" → 3 PM to 5 PM  
"now - 4pm" → now to 4 PM

---

2. Hour Ranges

Formats like "H to H", "H until H", "H til H", "H till H", "H untill H", or "H-H":

- Treated as the next available time window.
- If the second hour is smaller than the first, it wraps to the next day.

"10 to 2" → 10 PM today to 2 AM tomorrow  
"6-10" → 6 AM to 10 AM

---

3. Date Ranges

Formats like "MM/DD to MM/DD", "M/D - M/DD", or "MM/DD/YYYY to MM/DD/YYYY":

- Start time is 00:00 of the first day.
- End time is 23:59 of the last day.

"03/29 to 03/30" → 03-29-YYYY 00:00 to 03-30-YYYY 23:59  
"12/31/2025 to 01/01/2026" → 12-31-2025 00:00 to 01-01-2026 23:59

---

4. Durations

Formats like "MM mins", "MM minutes", "H hours", or "32min":

- Treated as a range starting now and lasting the specified duration.

"30 minutes" → now to now + 30 minutes  
"5 hours" → now to now + 5 hours  
"32min" → now to now + 32 minutes

---

5. Relative Time Words

- "in X minutes" or "in X hours" → starts X units from now, ends 1 hour later by default.
- "later today" or "later tonight" → starts at the next even hour (minimum 1 hour ahead), ends 2 hours later.
- If current time is past 10 PM, assume start is 8 AM tomorrow.

---

6. Day Names and Abbreviations

Accept full or abbreviated weekday names:

Monday, Mon  
Tuesday, Tue, Tues  
Wednesday, Wed, Weds  
Thursday, Thu, Thurs  
Friday, Fri  
Saturday, Sat  
Sunday, Sun

Also accept "tomorrow", "tom", "tmo" and "next [weekday]".

Always resolve to the next future occurrence.

If today is Wednesday and input is "Tuesday" → next Tuesday  
"tues 6 to 9" → next Tuesday 6 AM to 9 AM  
"tom 10 to 2" → tomorrow 10 AM to 2 PM

---

7. Natural Language Time Keywords

Convert the following to fixed times:

"noon" → 12:00  
"midnight" → 00:00  
"morning" → 08:00  
"afternoon" → 13:00  
"evening" → 18:00  
"night" → 21:00

"noon to 5" → 12:00 to 17:00  
"sat morning to noon" → next Saturday 08:00 to 12:00

---

8. “Now to [Time]” Format

For expressions like "now to", "now until", "now til", "now till", "now untill", or "now -":

- Start time of the time range is the current time.
- If the time is written as a single digit (e.g., “2”, “3”, etc.) 
  and it is ambiguous (no AM/PM), infer it as PM by default if current time is earlier than 12:00PM in the day.
- Times without AM/PM should be interpreted as the nearest future time.

"now to 2” at 6:00 AM → 06:00 to 14:00 today"
"now to 4" at 3:30 PM → 15:30 today to 16:00 today
"now to 4" at 4:30 PM → 4:30 today to 04:00 tomorrow   
"now to 2" at 3:30 PM → 15:30 today to 02:00 tomorrow
"now to 2" at 12:30 PM → 12:30 today to 14:00 today   
"now until noon" → now to 12:00 today if in future

---

9. Same-Day Weekday Ranges

If the start and end days are the same as today:

- Start = now
- End = same weekday next week at 23:59

On Saturday, "Saturday to Saturday" → now to next Saturday 23:59

---

10. Unsupported or Vague Expressions

If the expression is vague or open-ended, return "ERROR".

This includes:

"forever", "until further notice", "as long as needed", "whenever"

Also return "ERROR" if only a single fixed time is provided:

"8am", "Thursday 10am"

---

Constraints:

- DO NOT return any extra text, explanations, or timezone info.
- DO NOT return a time that is earlier than the current time (start or end).
- Use this current date and time: {current_time} (U.S. Central Time)
- If the expression is invalid or ambiguous, use {current_time} to infer missing parts.
- If that doesn’t help, return "ERROR".

---

Now process: "{time_str}"

"""




    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": prompt}]
    )

    formatted_time = response.choices[0].message.content.strip()
    logging.info(f"OpenAI raw response: {formatted_time}")

    if "to" in formatted_time:
        try:
            start_time_str, end_time_str = formatted_time.split(" to ")
            start_time = datetime.datetime.strptime(start_time_str, "%m-%d-%Y %H:%M")
            end_time = datetime.datetime.strptime(end_time_str, "%m-%d-%Y %H:%M")
            return f"{start_time.strftime('%m-%d-%Y %H:%M')} to {end_time.strftime('%m-%d-%Y %H:%M')}"
        except ValueError:
            logging.error(f"Malformed time range from OpenAI: {formatted_time}")
        return None
    else:
        try:
            datetime.datetime.strptime(formatted_time, "%m-%d-%Y %H:%M")
            return formatted_time
        except ValueError:
            logging.error(f"Malformed time from OpenAI: {formatted_time}")
        return None
    

async def rewrite_reservation_with_gpt(client: AsyncOpenAI, *, 
                                       original_text: str,
                                       choice: str,            # "start"|"end"|"range"
                                       new_value: str,
                                       tz_name: str = "America/Chicago") -> str:
    """
    Returns a full normalized range 'MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM' in tz_name.
    The model must output only that line.
    """
    # Get current time in the specified timezone for context
    central_tz = pytz.timezone(tz_name)
    current_time = datetime.datetime.now(central_tz).strftime("%m-%d-%Y %H:%M")
    current_day = datetime.datetime.now(central_tz).strftime("%A")  # e.g., "Saturday"
    
    sys = f"""You convert and rewrite human time requests into a single normalized range.
- Timezone: {tz_name}.
- Current date and time: {current_time} ({current_day})
- Output EXACTLY: 'MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM' with 24h minutes.
- When interpreting relative terms (today, tomorrow, next Tuesday, etc.), use the current date above.
- If input is ambiguous, infer the soonest valid future times by default.
- Never add text besides the one line.
"""
    user = f"""Current reservation text (verbatim):
<<<{original_text}>>>

User wants to change: {choice}
New value: {new_value}

Task: Rewrite the ENTIRE reservation as one normalized range for the same intent and tool.
Remember: Output ONLY the final normalized range."""
    rsp = await client.chat.completions.create(
        model="gpt-4o-mini",  # your current small model
        messages=[{"role":"system","content":sys},
                  {"role":"user","content":user}],
        temperature=0
    )
    return rsp.choices[0].message.content.strip()


async def parse_historical_time_range(time_str):
    """
    Uses OpenAI to parse time ranges for historical queries (allows past dates).
    Used for photo queries and other backward-looking searches.
    Returns MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM format.
    """
    central_tz = pytz.timezone("America/Chicago")
    current_time = datetime.datetime.now(central_tz).strftime("%m-%d-%Y %H:%M")

    prompt = f"""

You are a time range parser for historical data queries. Convert the following time expression into a standard format:

Times can be past, present, or future.

- Always return a range: MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM

---

Interpretation Rules:

1. Accepted Range Connectors

Interpret the following as equivalent: "to", "until", "til", "till", "untill", and "-".

"dec 10 to dec 15" → 12-10-YYYY 00:00 to 12-15-YYYY 23:59

---

2. Backward-Looking Relative Terms

"today" → 00:00 today to 23:59 today
"yesterday" → 00:00 yesterday to 23:59 yesterday
"last 3 days" → 00:00 three days ago to 23:59 today
"last week" → 00:00 seven days ago to 23:59 today
"last 2 weeks" → 00:00 fourteen days ago to 23:59 today
"last month" → 00:00 thirty days ago to 23:59 today
"this week" → 00:00 of most recent Monday to 23:59 today
"this month" → 00:00 of first day of current month to 23:59 today

---

3. Date Ranges

Formats like "MM/DD to MM/DD", "M/D - M/DD", or "MM/DD/YYYY to MM/DD/YYYY":

- Start time is 00:00 of the first day.
- End time is 23:59 of the last day.

"03/29 to 03/30" → 03-29-YYYY 00:00 to 03-30-YYYY 23:59  
"12/10/2025 to 12/15/2025" → 12-10-2025 00:00 to 12-15-2025 23:59
"dec 10 to dec 15" → 12-10-YYYY 00:00 to 12-15-YYYY 23:59

---

4. Single Day or Time

If only a single day is mentioned:

"december 15" → 12-15-YYYY 00:00 to 12-15-YYYY 23:59
"monday" → Most recent Monday 00:00 to 23:59
"12/15" → 12-15-YYYY 00:00 to 12-15-YYYY 23:59

---

5. Day Names and Abbreviations

Accept full or abbreviated weekday names:

Monday, Mon  
Tuesday, Tue, Tues  
Wednesday, Wed, Weds  
Thursday, Thu, Thurs  
Friday, Fri  
Saturday, Sat  
Sunday, Sun

For single weekdays, resolve to the most recent occurrence (can be today or in the past).

On Wednesday, "Monday" → most recent Monday 00:00 to 23:59
On Wednesday, "Thursday" → most recent Thursday (last week) 00:00 to 23:59

---

6. Natural Language Month Names

Accept full or abbreviated month names:

January, Jan  
February, Feb  
March, Mar  
April, Apr  
May  
June, Jun  
July, Jul  
August, Aug  
September, Sep, Sept  
October, Oct  
November, Nov  
December, Dec

"dec 1 to dec 15" → 12-01-YYYY 00:00 to 12-15-YYYY 23:59

---

Constraints:

- DO NOT return any extra text, explanations, or timezone info.
- ALWAYS return a time range, never a single time.
- Use this current date and time: {current_time} (U.S. Central Time)
- If the expression is invalid or ambiguous, return "ERROR".

---

Now process: "{time_str}"

"""

    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": prompt}]
    )

    formatted_time = response.choices[0].message.content.strip()
    logging.info(f"OpenAI historical time range response: {formatted_time}")

    if "to" in formatted_time and formatted_time != "ERROR":
        try:
            start_time_str, end_time_str = formatted_time.split(" to ")
            start_time = datetime.datetime.strptime(start_time_str, "%m-%d-%Y %H:%M")
            end_time = datetime.datetime.strptime(end_time_str, "%m-%d-%Y %H:%M")
            return f"{start_time.strftime('%m-%d-%Y %H:%M')} to {end_time.strftime('%m-%d-%Y %H:%M')}"
        except ValueError:
            logging.error(f"Malformed time range from OpenAI: {formatted_time}")
            return None
    else:
        logging.error(f"Invalid response from OpenAI: {formatted_time}")
        return None
