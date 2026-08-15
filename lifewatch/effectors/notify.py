"""The phone's channel: rung 2, a push notification.

The reference transport is ntfy, chosen for the same reason the rest of the
project is chosen: it is open source, self-hostable, needs no account and no
key, and a contributor can run it without signing up for anything. Nothing here
depends on that choice, though. The transport is injected as a callable taking
three plain strings, so replacing ntfy with a webhook, an SMS gateway, or a
local D-Bus notification is one function, not a refactor.

The body always carries the intervention's next action. That is the whole point
of the next-action gate in design spec section 9.2: a notification that says only
"you are behind" is a reproach, and reproach produces the avoidance this system
exists to treat. Loss and recovery appear in the same frame or not at all - and
the frame the user actually reads is this one, on their phone.
"""

from __future__ import annotations

from typing import Callable

import httpx

from lifewatch.config import Config
from lifewatch.effectors import Delivery
from lifewatch.models import Intervention

Poster = Callable[[str, str, str], bool]

REQUEST_TIMEOUT_S = 10.0


def post_via_httpx(url: str, body: str, title: str) -> bool:
    """Default transport: a plain HTTP POST, ntfy-shaped.

    Bounded by an explicit timeout because the caller is a watcher loop; a push
    service that hangs must cost one delivery, not the loop's ability to keep
    watching. The title travels as a header, so it is transliterated to ASCII -
    headers are latin-1 on the wire and a title that cannot be encoded would
    fail the whole delivery over decoration.
    """
    safe_title = title.encode("ascii", "replace").decode("ascii")
    response = httpx.post(
        url,
        content=body.encode("utf-8"),
        headers={"Title": safe_title, "Content-Type": "text/plain; charset=utf-8"},
        timeout=REQUEST_TIMEOUT_S,
    )
    return response.is_success


class NotifyEffector:
    name = "notify"

    def __init__(self, config: Config, poster: Poster = post_via_httpx) -> None:
        self.config = config
        self.poster = poster

    def available(self) -> bool:
        """False until a push topic is configured.

        Unconfigured means skipped, never guessed. There is no default endpoint
        because a default endpoint is a URL shipped in source, and this project
        ships no personal value at all.
        """
        return bool(self.config.notify_url)

    def deliver(self, iv: Intervention) -> Delivery:
        url = self.config.notify_url
        if not url:
            return Delivery(effector=self.name, ok=False,
                            detail="no notify_url configured")
        try:
            sent = self.poster(url, self.body_for(iv), self.title_for(iv))
        except Exception as exc:
            # Every exception, deliberately. A transport can fail in ways this
            # module has never heard of - DNS, TLS, a proxy returning HTML - and
            # none of them are worth taking the watcher down for.
            return Delivery(effector=self.name, ok=False, detail=str(exc))
        if not sent:
            return Delivery(effector=self.name, ok=False,
                            detail="transport reported the message was not sent")
        return Delivery(effector=self.name, ok=True)

    @staticmethod
    def title_for(iv: Intervention) -> str:
        return f"lifewatch - rung {iv.rung}"

    @staticmethod
    def body_for(iv: Intervention) -> str:
        """What went wrong, then the concrete thing to do about it.

        The next action goes last so the message never ends on the complaint.
        It is the line the reader is left holding, and it is what turns an
        accusation into an instruction. It is also unconditional: the message
        may be empty, the next action never can be.
        """
        parts = []
        if iv.message.strip():
            parts.append(iv.message.strip())
        parts.append(f"Next: {iv.next_action.strip()}")
        return "\n\n".join(parts)
