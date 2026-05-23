"""Common test setup: adds backend/ to sys.path."""
import os
import sys

HERE = os.path.dirname(__file__)
BACKEND = os.path.abspath(os.path.join(HERE, "..", "backend"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
