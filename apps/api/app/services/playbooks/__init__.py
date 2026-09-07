"""Versioned industry playbooks — V3.6.

Importing the package registers the five shipped playbooks, so ``registry.select`` works
without a caller remembering to wire them and a duplicate id/version is an import-time
error rather than a silent overwrite.
"""

from app.services.playbooks.industries import register_all
from app.services.playbooks.registry import all_playbooks, get, select
from app.services.playbooks.schema import (
    AppliesTo,
    Playbook,
    PlaybookQuestion,
    PlaybookSelection,
)

register_all()

__all__ = [
    "AppliesTo",
    "Playbook",
    "PlaybookQuestion",
    "PlaybookSelection",
    "all_playbooks",
    "get",
    "register_all",
    "select",
]
