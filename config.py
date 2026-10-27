"""
Central config for the booking bot.
Zero logic here — constants only. Everything else imports from this file.
"""
from zoneinfo import ZoneInfo

# --- Timezone ---
# America/Mexico_City, NOT America/Chicago: Mexico abolished DST in 2022,
# US Central didn't. The two zones differ part of the year.
TZ = ZoneInfo("America/Mexico_City")

# --- Sessions ---
SESSION_MINUTES = 90  # real sessions run 60-90 min; 90 is the safe calendar block

# --- Business hours ---
BUSINESS_OPEN_HOUR = 9        # 9:00 am
BUSINESS_CLOSE_HOUR = 20      # 8:00 pm — the session must END by this time
LUNCH_START = (13, 0)         # 1:00 pm
LUNCH_END = (14, 30)          # 2:30 pm

# --- Holidays as (month, day). Edit per year / business policy ---
HOLIDAYS = {
    (1, 1),    # New Year
    (2, 5),    # Constitution Day (official date)
    (3, 21),   # Benito Juarez (official date)
    (5, 1),    # Labor Day
    (9, 16),   # Independence Day
    (11, 20),  # Revolution Day (official date)
    (12, 12),  # Virgen de Guadalupe
    (12, 25),  # Christmas
}

# --- Conversation ---
CONVERSATION_TIMEOUT_SECONDS = 90   # inactivity before an in-progress flow is cancelled
TIMEOUT_SWEEP_INTERVAL = 15         # local sweeper thread check interval (seconds)
MAX_JUNK_STRIKES = 3                # consecutive junk inputs in ASK_DATETIME before the chat is closed

# --- Google Calendar ---
CALENDAR_ID = "primary"
GOOGLE_TOKEN_FILE = "token.json"    # same OAuth file your Telegram version uses
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]
BOT_EVENT_TAG = "agenda-bot"        # marks events created by this bot

# --- WhatsApp Cloud API (environment variables) ---
# WHATSAPP_TOKEN      -> permanent System User token from Meta
# PHONE_NUMBER_ID     -> ID of the business phone number
# VERIFY_TOKEN        -> any string you invent, used in the webhook handshake
GRAPH_API_VERSION = "v20.0"

# --- Reminders ---
REMINDER_WINDOW_HOURS = (23, 25)  # events starting 23-25h from now get reminded
REMINDER_TEMPLATE_NAME = "recordatorio_cita"  # pre-approved template in Meta
