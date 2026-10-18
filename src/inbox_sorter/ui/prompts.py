"""The setup wizard's prompts: arrow-key menus in a terminal, line prompts otherwise.

Both implement ``setup.Prompter`` (A-setup-prompter). questionary is imported
only inside ``MenuPrompter``'s methods, so line mode never loads it.
"""

from collections.abc import Callable, Sequence
from typing import Any, TextIO

from inbox_sorter.gmail import LabelInfo
from inbox_sorter.setup import SetupCancelled
from inbox_sorter.types import Email
from inbox_sorter.ui.table import COLUMNS, fit, one_line

SENDER_WIDTH = COLUMNS[0][1]
SUBJECT_WIDTH = COLUMNS[1][1]

CANCELLED = "setup cancelled; nothing was written"
INPUT_ENDED = "input ended before setup finished; nothing was written"
NOT_TEXT = "input is not UTF-8 text; nothing was written"


def preview_lines(label: LabelInfo, samples: Sequence[Email]) -> list[str]:
    """``<label> · <n> messages``, then one sender and subject line per sample.

    Headers come from senders, so every field is folded to one line with
    unsafe characters shown as "?".
    """
    lines = [f"{one_line(label.name)} · {label.message_count} messages"]
    lines += [
        f"  {fit(sample.sender, SENDER_WIDTH)}  {fit(sample.subject, SUBJECT_WIDTH)}".rstrip()
        for sample in samples
    ]
    return lines


class LinePrompter:
    """Numbered and [default] prompts on plain lines; Enter takes the default."""

    def __init__(self, inp: TextIO, out: TextIO) -> None:
        self.inp = inp
        self.out = out

    def preview(self, label: LabelInfo, samples: Sequence[Email]) -> None:
        self._write("")
        for line in preview_lines(label, samples):
            self._write(line)

    def confirm(self, question: str, default: bool) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self._ask(f"{one_line(question)} {hint}: ").strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("answer y or n")

    def text(self, question: str, default: str) -> str:
        hint = f" [{one_line(default)}]" if default else ""
        answer = self._ask(f"{one_line(question)}{hint}: ")
        return answer if answer.strip() else default

    def select(self, question: str, choices: Sequence[str], default: str) -> str:
        for number, choice in enumerate(choices, start=1):
            self._write(f"  {number}) {one_line(choice)}")
        default_number = choices.index(default) + 1
        while True:
            answer = self._ask(f"{one_line(question)} [{default_number}]: ").strip()
            if not answer:
                return default
            if answer.isdecimal() and 1 <= int(answer) <= len(choices):
                return choices[int(answer) - 1]
            self.say(f"enter a number from 1 to {len(choices)}")

    def say(self, line: str) -> None:
        self._write(one_line(line))

    def _ask(self, prompt: str) -> str:
        print(prompt, end="", file=self.out, flush=True)
        try:
            line = self.inp.readline()
        except KeyboardInterrupt:
            raise SetupCancelled(CANCELLED) from None
        except UnicodeDecodeError:
            raise SetupCancelled(NOT_TEXT) from None
        if not line:
            raise SetupCancelled(INPUT_ENDED)
        return line.rstrip("\r\n")

    def _write(self, line: str) -> None:
        print(line, file=self.out, flush=True)


class MenuPrompter:
    """Arrow-key menus (ux.md §Setup wizard); needs a terminal on stdin and stdout."""

    def __init__(self, out: TextIO) -> None:
        self.out = out

    def preview(self, label: LabelInfo, samples: Sequence[Email]) -> None:
        print(file=self.out)
        for line in preview_lines(label, samples):
            print(line, file=self.out, flush=True)

    def confirm(self, question: str, default: bool) -> bool:
        import questionary

        return _ask(questionary.confirm, one_line(question), default=default)

    def text(self, question: str, default: str) -> str:
        import questionary

        return _ask(questionary.text, one_line(question), default=default)

    def select(self, question: str, choices: Sequence[str], default: str) -> str:
        import questionary

        return _ask(questionary.select, one_line(question), choices=list(choices), default=default)

    def say(self, line: str) -> None:
        print(one_line(line), file=self.out, flush=True)


def _ask(make: Callable[..., Any], question: str, **options: Any) -> Any:
    try:
        return make(question, **options).unsafe_ask()
    except (KeyboardInterrupt, EOFError):
        raise SetupCancelled(CANCELLED) from None
