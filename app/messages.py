"""Plain data types shared by the conversation logic and the WhatsApp client."""
from dataclasses import dataclass, field


@dataclass
class Incoming:
    phone: str
    text: str = ""
    reply_id: str = ""  # id of a tapped button / list row
    name: str = ""  # WhatsApp profile name


@dataclass
class Out:
    to: str
    kind: str  # "text" | "buttons" | "list"
    body: str
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (id, title)
    rows: list[dict] = field(default_factory=list)  # {"id", "title", "description"}
    list_button: str = "Choose"


def say(to: str, body: str) -> Out:
    return Out(to, "text", body)


def btns(to: str, body: str, items: list[tuple[str, str]]) -> Out:
    return Out(to, "buttons", body, buttons=list(items))


def menu_list(to: str, body: str, label: str, items: list[dict]) -> Out:
    return Out(to, "list", body, rows=items, list_button=label)
