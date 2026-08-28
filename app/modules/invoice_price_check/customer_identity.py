from __future__ import annotations

import re
from collections.abc import Iterable


def normalized_customer_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def customer_name_signature(value: str) -> tuple[str, ...]:
    """Return a conservative, order-independent company-name signature.

    Punctuation, parentheses, whitespace, token order, and the common P/L vs PL
    spelling are ignored. A signature is accepted only when it identifies one
    authoritative customer name, so this never chooses between collisions.
    """

    text = value.casefold()
    text = re.sub(r"p\s*[./\\]\s*l", "pl", text)
    text = text.replace("'", "").replace("’", "")
    tokens = re.findall(r"[a-z0-9]+", text)
    return tuple(sorted(tokens))


def resolve_unique_customer_name(
    requested: str,
    available_names: Iterable[str],
) -> str | None:
    names = sorted(set(available_names), key=lambda value: value.casefold())
    exact = [
        name
        for name in names
        if normalized_customer_name(name) == normalized_customer_name(requested)
    ]
    if len(exact) == 1:
        return exact[0]
    signature = customer_name_signature(requested)
    if not signature:
        return None
    matches = [name for name in names if customer_name_signature(name) == signature]
    return matches[0] if len(matches) == 1 else None
