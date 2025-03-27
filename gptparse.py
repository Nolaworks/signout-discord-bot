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

- All times will be either present or future.
- If it's a single time, return (MM-DD-YYYY HH:MM).
- If it's a time range, return (MM-DD-YYYY HH:MM to MM-DD-YYYY HH:MM).

Interpretation Rules:

1. Hour Ranges:
   - Formats like "H to H", "H until H", or "H-H" (e.g., "3 to 5", "3-5"):
     - Treated as the next available time window.
     - If the second hour is smaller than the first, it wraps to the next day.
     - Examples:
       - "10 to 2" → 10 PM today to 2 AM tomorrow
       - "6-10" → 6 AM to 10 AM

2. Date Ranges:
   - Formats like "MM/DD to MM/DD" or "M/D - M/DD":
     - Interpreted as a range from 00:00 on the first date to 23:59 on the second.
     - Example: "03/29 to 03/30" → 03-29-YYYY 00:00 to 03-30-YYYY 23:59

3. Durations:
   - Formats like "MM mins", "MM minutes", "H hours", or forms like "32min":
     - Treated as a range starting now, lasting the specified duration.
     - Examples:
       - "30 minutes" → now to now + 30 minutes
       - "5 hours" → now to now + 5 hours
       - "32min" or "32 min" → now to now + 32 minutes

4. Relative Time Words:
   - "in X minutes" or "in X hours":
     - Starts X units from now, lasts 1 hour by default unless stated otherwise.
     - Example: "in 2 hours" → now + 2 hours to now + 3 hours
   - "later today" or "later tonight":
     - Starts at the next even hour after now (minimum 1 hour ahead), ends 2 hours later.
     - If past 10 PM, assume start is 8 AM tomorrow.

5. Day Names and Abbreviations:
   - Accept full or abbreviated weekday names, including variations:
     - Monday, Mon
     - Tuesday, Tue, Tues
     - Wednesday, Wed, Weds
     - Thursday, Thu, Thurs
     - Friday, Fri
     - Saturday, Sat
     - Sunday, Sun
   - Also accept "tomorrow", "tom", and "next [weekday]".
   - Always resolve the weekday to the **next future occurrence**, even crossing into a new month.
     - Examples:
       - If today is Wednesday and input is "Tuesday", return next Tuesday.
       - "weds 6pm to 8" sent on 03-27-2025 → next Wednesday 6pm to 8pm, which is 04-02-2025
       - "tues 6 to 9" → next Tuesday 6 AM to 9 AM
       - "weds 3-5" → next Wednesday 3 AM to 5 AM
       - "tom 10 to 2" → tomorrow 10 AM to 2 PM

6. Natural Language Time Keywords:
   - Recognize common time keywords and convert them to clock times:
     - "noon" → 12:00
     - "midnight" → 00:00
     - "morning" → 08:00 (start), "afternoon" → 13:00, "evening" → 18:00, "night" → 21:00
     - Examples:
       - "noon to 5" → 12:00 to 17:00
       - "midnight to 3" → 00:00 to 03:00
       - "sat morning to noon" → next Saturday 08:00 to 12:00

7. Unsupported or Vague Expressions:
   - If the input contains vague or open-ended terms such as "forever", "until further notice", "as long as needed", or "whenever", return "ERROR".
   - Example: "Friday 10am to forever" → ERROR

Constraints:

- - If the time range spans more than one calendar year, return "ERROR".
   - Example: "12/31/2025 to 01/01/2026" → ERROR
- DO NOT return any extra text, explanations, or timezone information.
- DO NOT EVER return a time that is earlier than the current time. This must be maintained for both the start time and end time of a range.
- Use the current date and time: {current_time} (U.S. Central Time) as a reference.
- If the expression is invalid or ambiguous, use {current_time} to fill in missing parts.
- If this doesn't help, return "ERROR."

Now process: {time_str}
"""



    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
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