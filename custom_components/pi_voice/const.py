"""Constants for the pi-voice Conversation integration."""

DOMAIN = "pi_voice"

CONF_URL = "url"
CONF_API_KEY = "api_key"
CONF_PROMPT = "prompt"
CONF_TIMEOUT = "timeout"
CONF_HANDOFF = "handoff_after"

DEFAULT_TIMEOUT = 600
DEFAULT_HANDOFF = 15
MAX_HANDOFF = 29
DEFAULT_PROMPT = (
    "You are answering through a smart speaker via Home Assistant Assist. "
    "Reply in one to three short, natural spoken sentences without markdown, "
    "lists, code, URLs, emoji or tables. End with a question only when you "
    "need the user's answer; ending with a question keeps the microphone open."
)

HANDOFF_MESSAGE = "I'm working on that. I'll let you know when it's done."
NO_ANSWER_MESSAGE = "I didn't get an answer back."
