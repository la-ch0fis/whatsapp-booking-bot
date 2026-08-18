"""
messages.py — Every user-facing string in one place. These stay in
SPANISH on purpose: the bot serves Spanish-speaking customers.
One DISTINCT message per failure reason; no generic catch-all errors.
"""
import parsing_utils as p

WELCOME = (
    "¡Hola! 🌿 Soy el asistente de citas.\n\n"
    "Escribe *agendar* para reservar una sesión (Reiki / Biomagnetismo)\n"
    "Escribe *cancelar* para cancelar una cita existente"
)

REDIRECT = (
    "No entendí ese mensaje 🤔\n\n"
    "Puedo ayudarte con dos cosas:\n"
    "• *agendar* — reservar una sesión\n"
    "• *cancelar* — cancelar una cita"
)

ASK_NAME = "¡Perfecto! ¿A nombre de quién agendo la sesión?"

ASK_DATETIME = (
    "Gracias, {name} 🙌\n\n"
    "¿Qué día y a qué hora te gustaría tu sesión?\n"
    "Ejemplos: *27 de julio, 3pm* · *mañana a las 4pm* · *05/08/2026 10:30am*"
)

PARSE_ERRORS = {
    p.ERR_JUNK: (
        "Hay algo en tu mensaje que no reconozco 😅 "
        "Escríbeme solo la fecha y la hora, por ejemplo: *28 de julio, 5pm*"
    ),
    p.ERR_NO_TIME_MARKER: (
        "No me quedó clara la hora ⏰ Indícame *am* o *pm*, "
        "o usa formato con dos puntos, por ejemplo: *3pm* o *15:00*"
    ),
    p.ERR_INVALID_DATE: (
        "Esa fecha no existe en el calendario 📅 "
        "Revísala e inténtalo de nuevo (por ejemplo: *28 de julio, 5pm*)"
    ),
    p.ERR_MISSING_PARTS: (
        "Me falta parte de la información 🤏 "
        "Mándame fecha y hora juntas, por ejemplo: *mañana a las 4pm*"
    ),
    p.ERR_PAST: "Esa fecha u hora ya pasó ⏳ Elige un momento futuro, por favor.",
    p.ERR_SUNDAY: "Los domingos descansamos 🙏 Elige otro día de lunes a sábado.",
    p.ERR_HOLIDAY: "Ese día es festivo y no habrá servicio 🎉 ¿Te queda otro día?",
    p.ERR_LUNCH: (
        "Ese horario cae en la hora de comida (1:00–2:30 pm) 🍽 "
        "Elige un horario que no la cruce."
    ),
    p.ERR_OUTSIDE_HOURS: (
        "Atendemos de 9:00 am a 8:00 pm y la sesión dura hasta 90 min, "
        "así que la última cita inicia a las 6:30 pm. ¿Otro horario?"
    ),
    p.ERR_UNPARSEABLE: (
        "No logré entender la fecha 😅 "
        "Prueba con algo como: *27 de julio, 3pm* o *mañana a las 4pm*"
    ),
}

CONFIRM_SLOT = "✅ Tengo disponible el *{when}*.\n¿Confirmo tu sesión?"

SLOT_TAKEN_WITH_ALTS = (
    "Ese horario ya está ocupado 😔 "
    "Pero ese mismo día tengo estos espacios libres:"
)

SLOT_TAKEN_NO_ALTS = (
    "Ese horario ya está ocupado y no me quedan espacios libres ese día 😔 "
    "¿Quieres intentar con otra fecha? Escríbeme fecha y hora."
)

BOOKED = (
    "¡Listo! 🎉 Tu sesión quedó agendada para el *{when}*.\n"
    "Te enviaré un recordatorio un día antes. ¡Nos vemos!"
)

BOOKING_ABORTED = "Sin problema, no agendé nada. Escribe *agendar* cuando quieras intentar de nuevo."

TIMEOUT = (
    "Pasaron más de 90 segundos sin respuesta, así que cancelé el proceso ⏱ "
    "Escribe *agendar* o *cancelar* cuando quieras retomarlo."
)

CANCEL_NONE = "No encontré citas futuras a tu nombre 🤷 No hay nada que cancelar."

CANCEL_PICK = "Estas son tus próximas citas. ¿Cuál quieres cancelar?"

CANCEL_CONFIRM = "¿Seguro que cancelo tu sesión del *{when}*?"

CANCELLED = "Tu cita del *{when}* quedó cancelada ✅ Escribe *agendar* si quieres una nueva."

CANCEL_KEPT = "Perfecto, tu cita sigue en pie 👍"

CANCEL_FAILED = "No pude cancelar esa cita (quizá ya pasó o ya no existe). Escribe *cancelar* para ver tus citas actuales."

EXTERNAL_ERROR = (
    "Uy, tuve un problema técnico al consultar la agenda 🛠 "
    "Inténtalo de nuevo en un momento, por favor."
)

DAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def fmt_dt(dt) -> str:
    """'martes 28 de julio, 3:00 pm' — human-friendly Spanish format."""
    hour12 = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    return (
        f"{DAYS_ES[dt.weekday()]} {dt.day} de {MONTHS_ES[dt.month]}, "
        f"{hour12}:{dt.minute:02d} {ampm}"
    )


def fmt_time(dt) -> str:
    hour12 = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    return f"{hour12}:{dt.minute:02d} {ampm}"
