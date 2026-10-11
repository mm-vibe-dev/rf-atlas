"""Compatibility entry point for the original RF Atlas CLI."""
import sys
from rfatlas import legacy
if __name__ == "__main__":
    sys.exit(legacy.main())
else:
    sys.modules[__name__] = legacy
