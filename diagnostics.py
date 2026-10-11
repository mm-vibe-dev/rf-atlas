"""Compatibility import for existing local diagnostic scripts."""
import sys
from rfatlas import diagnostics
sys.modules[__name__] = diagnostics
