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
        - If the input follows the format "H to H" or "H until H" (e.g., "3 to 5" or "3 until 5"), interpret it as the next available time range that starts with the first number.
            - If the second number is smaller than the first, assume it refers to the following day (e.g., "10 to 2" means 10 PM to 2 AM the next day).
        - If the input follows the format "MM/DD to MM/DD" or any variation of it (e.g., "M/D - M/DD"), treat it as a time range starting at 00:00 of the first date.
        - If the input follows the format "MM mins (or minutes)" or "H hours" (e.g., "30 minutes" or "5 hours"), treat it as a range starting immediately, extending for the length the input specifies.
        - If this results in crossing midnight, adjust the date accordingly.
        - DO NOT return any extra text, explanations, or timezone information.
        - DO NOT return a time that is earlier than the current time.

        Use the current date and time: {current_time} (U.S. Central Time) as a reference.
        If the expression is invalid or ambiguous, use {current_time} to fill in missing parts.
        If this doesn't help, return "ERROR."

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