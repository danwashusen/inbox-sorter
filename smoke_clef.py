"""Smoke test: load Cloudflare/clef (backbone + joint schema head) and classify sample emails."""

import json
import sys
import time

import torch
from huggingface_hub import snapshot_download

from inbox_sorter.clef_request import build_request

MODEL_ID = "Cloudflare/clef"

QUESTIONS = {
    "category": {
        "type": "choice",
        "instructions": "What kind of email is this?",
        "criteria": {
            "newsletter": "Bulk mailing, newsletter or marketing",
            "receipt": "Order confirmation, invoice or receipt",
            "personal": "Written by a person to me",
            "notification": "Automated alert or notice from a service",
        },
    },
    "needs_reply": {"type": "noul", "instructions": "Does the sender expect me to reply?"},
    "urgency": {
        "type": "score",
        "instructions": "How soon does this need my attention?",
        "criteria": ["Can wait", "This week", "Today"],
    },
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
    path = snapshot_download(MODEL_ID)
    sys.path.insert(0, path)
    from joint_schema_model import load_release_model, systemone

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"device={device} torch={torch.__version__}")

    start = time.perf_counter()
    model, processor = load_release_model(path, device=device, dtype=torch.bfloat16)
    print(f"loaded in {time.perf_counter() - start:.1f}s\n")

    for email in EMAILS:
        start = time.perf_counter()
        response = systemone(model, processor, build_request(email, QUESTIONS))
        elapsed = time.perf_counter() - start
        print(f"== {email['subject']}  ({elapsed:.2f}s, {response['usage']['input_tokens']} tokens)")
        print(json.dumps(response["answers"], indent=2))
        print()


if __name__ == "__main__":
    main()
