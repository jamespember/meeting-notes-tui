#!/usr/bin/env python3
"""Entry point for Omascribe application."""

import sys
from omascribe.app import run

if __name__ == "__main__":
    # Parse args for --dev flag
    dev_mode = '--dev' in sys.argv
    
    run(dev_mode=dev_mode)
