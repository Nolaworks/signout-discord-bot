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
     - Example: "10 to 2" = 10 PM today to 2 AM tomorrow.

2. Date Ranges:
   - Formats like "MM/DD to MM/DD" or "M/D - M/DD":
     - Interpreted as a range from 00:00 on the first date to 23:59 on the second.

3. Durations:
   - Formats like "MM mins", "MM minutes", "H hours":
     - Treated as a range starting now, lasting the specified duration.

4. Relative Time Words:
   - "in X minutes" or "in X hours":
     - Starts X units from now, lasts 1 hour by default unless stated otherwise.
     - Example: "in 2 hours" → starts now + 2 hours, lasts 1 hour.
   - "later today" or "later tonight":
     - Starts at the next even hour after now (minimum 1 hour ahead), ends 2 hours later.
     - If past 10 PM, assumes tomorrow morning at 8 AM for start.

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
   - Always resolve the weekday to the **next future occurrence**.
     - Example: If today is Wednesday and input is "Tuesday", return next Tuesday.
   - Examples:
     - "tues 6 to 9" → next Tuesday 6 AM to 9 AM
     - "weds 3-5" → next Wednesday 3 AM to 5 AM
     - "tom 10 to 2" → tomorrow 10 AM to 2 PM

Constraints:

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