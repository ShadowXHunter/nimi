"""Messaging providers. Register new ones in PROVIDERS."""
from .base import Message, Provider, Reaction
from .gchat import GChatProvider

PROVIDERS = {
    GChatProvider.name: GChatProvider,
    # "slack": SlackProvider,   # future
    # "teams": TeamsProvider,   # future
}


def get_provider(name: str, settings) -> Provider:
    try:
        return PROVIDERS[name](settings)
    except KeyError:
        raise SystemExit(f"unknown provider {name!r}; known: {', '.join(sorted(PROVIDERS))}")


__all__ = ["Message", "Provider", "Reaction", "PROVIDERS", "get_provider"]
