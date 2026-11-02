"""Start the app: python -m tenderdesk [data.db] [--port 8047]"""
import sys

from .fieldkit import main_cli
from .web import create_app

sys.exit(main_cli(create_app, "tender.db", 8047, sys.argv))
