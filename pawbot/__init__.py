"""
pawbot - A lightweight AI agent framework
"""
import sys

__version__ = "0.1.4.post3"
__logo__ = "🐾"

if sys.platform == "win32":
    if "utf" not in getattr(sys.stdout, "encoding", "").lower():
        __logo__ = "[Pawbot]"
