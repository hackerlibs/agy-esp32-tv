#!/usr/bin/env python3
"""Compatibility wrapper: forwards to host/agy_tv.py."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import agy_tv  # noqa: E402

if __name__ == "__main__":
    sys.exit(agy_tv.main())
