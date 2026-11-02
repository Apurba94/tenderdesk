"""TenderDesk: procurement records with a tamper-evident audit trail."""
from . import core
from .web import create_app

__all__ = ["core", "create_app"]
