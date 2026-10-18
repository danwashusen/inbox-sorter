"""Build the three questions every message is asked (prd.md §Clef questions)."""

from inbox_sorter.config import Config, ConfigError
from inbox_sorter.engine import Choice, Noul, Question, Score

CATEGORY = "category"
NEEDS_REPLY = "needs_reply"
URGENCY = "urgency"
URGENCY_LEVELS = ("Can wait", "This week", "Today")


def build_questions(config: Config) -> dict[str, Question]:
    """The category, needs_reply and urgency questions, offering only enabled categories."""
    options = {c.id: c.description for c in config.categories if c.enabled}
    if not options:
        raise ConfigError("no category is enabled; set enabled = true on at least one [[category]]")
    return {
        CATEGORY: Choice(options, instructions="What kind of email is this?"),
        NEEDS_REPLY: Noul("Does the sender expect me to reply?"),
        URGENCY: Score(URGENCY_LEVELS, instructions="How soon does this need my attention?"),
    }
