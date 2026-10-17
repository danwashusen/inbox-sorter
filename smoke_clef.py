"""Smoke test: classify sample emails with the local clef decision engine."""

import json
import time
from dataclasses import asdict

from inbox_sorter.engine import Choice, DecisionRequest, Noul, Score
from inbox_sorter.engines import make_engine
from inbox_sorter.state import email_state

QUESTIONS = {
    "category": Choice(
        {
            "newsletter": "Bulk mailing, newsletter or marketing",
            "receipt": "Order confirmation, invoice or receipt",
            "personal": "Written by a person to me",
            "notification": "Automated alert or notice from a service",
        },
        instructions="What kind of email is this?",
    ),
    "needs_reply": Noul("Does the sender expect me to reply?"),
    "urgency": Score(
        ("Can wait", "This week", "Today"),
        instructions="How soon does this need my attention?",
    ),
}

EMAILS = [
    {
        "from": "The Weekly Byte <news@weeklybyte.io>",
        "subject": "This week: 10 Python tricks you missed",
        "snippet": "Plus our sponsor's spring sale. Unsubscribe at any time.",
    },
    {
        "from": "Amazon.co.uk <auto-confirm@amazon.co.uk>",
        "subject": "Your order #204-1234567 has been dispatched",
        "snippet": "Order total £23.99. Arriving Tuesday. View or manage your order.",
    },
    {
        "from": "Sarah Chen <sarah.chen@gmail.com>",
        "subject": "Dinner on Friday?",
        "snippet": "Hey! Are you and Alex free Friday evening? Let me know by Wednesday so I can book.",
    },
    {
        "from": "GitHub <noreply@github.com>",
        "subject": "[Action required] Unusual sign-in to your account",
        "snippet": "We noticed a sign-in from a new device in Frankfurt. If this wasn't you, reset your password now.",
    },
]


def main() -> None:
    start = time.perf_counter()
    engine = make_engine("clef-local")
    print(f"engine={engine.name} device={engine.device}")
    print(f"loaded in {time.perf_counter() - start:.1f}s\n")

    try:
        for email in EMAILS:
            start = time.perf_counter()
            response = engine.decide(DecisionRequest(email_state(email), QUESTIONS))
            elapsed = time.perf_counter() - start
            print(f"== {email['subject']}  ({elapsed:.2f}s)")
            answers = {qid: asdict(answer) for qid, answer in response.answers.items()}
            print(json.dumps(answers, indent=2))
            print()
    finally:
        engine.close()


if __name__ == "__main__":
    main()
