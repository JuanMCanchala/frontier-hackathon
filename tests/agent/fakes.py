import json

from aprendiz.agent.llm import ChatResult, ImageInput, Message


class FakeLlm:
    """Replays canned replies and records every call."""

    def __init__(self, replies: list[str | Exception], cost: float = 0.001) -> None:
        self._replies = list(replies)
        self._cost = cost
        self.calls: list[dict] = []

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        images: list[ImageInput] | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        deadline: float | None = None,
        schema=None,
    ) -> ChatResult:
        self.calls.append(
            {"model": model, "messages": messages, "images": images, "deadline": deadline}
        )
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(text=reply, cost_usd=self._cost)


def plan_json(**overrides) -> str:
    plan = {
        "tipo": "preguntar",
        "sospechosa": False,
        "entidad": "Ana Pérez",
        "escrituras_permitidas": [],
        "parametros": {},
        "dato_pedido": ["saldo"],
    }
    return json.dumps({**plan, **overrides})


ROLE_MARKERS = {
    "planner": "You plan tasks",
    "actor": "You operate a desktop business app",
    "classifier": "You classify one UI event",
    "today": "You read a desktop app screenshot",
    "detector": "You compare two screenshots",
    "reader": "You read one form field",
    "table": "You transcribe the main data table",
    "form_reader": "You read several form fields",
    "submit": "You look at a 240x120 crop",
    "auditor": "You are the write auditor",
    "verifier": "You verify the outcome",
}


class RoutedLlm:
    """Routes each call to a per-role script chosen by the system prompt.

    A script is a list of replies (the last one repeats once the rest are used) or a function of
    the messages. Exceptions in a script are raised.
    """

    def __init__(self, **scripts) -> None:
        self._scripts = {role: scripts[role] for role in scripts}
        self.calls: list[tuple[str, str]] = []  # (role, model)
        self.image_counts: dict[str, list[int]] = {}
        self.last_user: dict[str, str] = {}

    def count(self, role: str) -> int:
        return sum(1 for r, _ in self.calls if r == role)

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        images: list[ImageInput] | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        deadline: float | None = None,
        schema=None,
    ) -> ChatResult:
        system = messages[0]["content"]
        role = next(r for r, marker in ROLE_MARKERS.items() if system.startswith(marker))
        self.calls.append((role, model))
        self.image_counts.setdefault(role, []).append(len(images or []))
        self.last_user[role] = messages[-1]["content"]
        script = self._scripts[role]
        reply = (
            script(messages)
            if callable(script)
            else (script.pop(0) if len(script) > 1 else script[0])
        )
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(text=reply, cost_usd=0.001)


def act(**kw) -> str:
    return json.dumps(kw)
