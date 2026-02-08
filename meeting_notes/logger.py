"""Centralized logging configuration for Meeting Notes application."""

import logging
import os
from pathlib import Path
from datetime import datetime


def get_log_dir() -> Path:
    """Get the directory for log files."""
    config_home = os.environ.get('XDG_CONFIG_HOME')
    if config_home:
        log_dir = Path(config_home) / "meeting-notes"
    else:
        log_dir = Path.home() / ".config" / "meeting-notes"
    
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(debug: bool = False) -> None:
    """
    Set up application-wide logging.
    
    Creates two log files:
    - errors.log: Only ERROR and CRITICAL messages (always enabled)
    - meeting-notes.log: All messages including INFO and DEBUG (daily rotation)
    
    Args:
        debug: If True, set console output to DEBUG level
    """
    log_dir = get_log_dir()
    
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture everything
    
    # Clear any existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # 1. Console handler - INFO or DEBUG depending on debug flag
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # 2. Error file handler - Only errors and above
    error_log = log_dir / "errors.log"
    error_handler = logging.FileHandler(error_log)
    error_handler.setLevel(logging.ERROR)
    error_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s\n'
        'File: %(pathname)s:%(lineno)d\n'
    )
    error_handler.setFormatter(error_formatter)
    root_logger.addHandler(error_handler)
    
    # 3. Full application log - All messages
    app_log = log_dir / "meeting-notes.log"
    app_handler = logging.FileHandler(app_log)
    app_handler.setLevel(logging.DEBUG)
    app_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    app_handler.setFormatter(app_formatter)
    root_logger.addHandler(app_handler)
    
    # Log startup
    logging.info("="*80)
    logging.info(f"Meeting Notes application started at {datetime.now()}")
    logging.info(f"Log directory: {log_dir}")
    logging.info(f"Debug mode: {debug}")
    logging.info("="*80)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific module.
    
    Args:
        name: Usually __name__ of the module
        
    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
