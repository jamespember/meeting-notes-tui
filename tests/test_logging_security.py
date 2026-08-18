"""Tests for private logs and complete credential redaction."""

import logging

from meeting_notes.config import AppConfig
from meeting_notes import logger


def test_api_keys_are_fully_redacted():
    config = AppConfig(anthropic_api_key="sk-ant-secret-value")
    safe = config.to_safe_dict()
    assert safe["anthropic_api_key"] == "[redacted]"
    assert "secret" not in str(safe)


def test_log_files_are_private(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    logger.setup_logging()

    log_dir = tmp_path / "meeting-notes"
    assert log_dir.stat().st_mode & 0o777 == 0o700
    assert (log_dir / "meeting-notes.log").stat().st_mode & 0o777 == 0o600
    assert (log_dir / "errors.log").stat().st_mode & 0o777 == 0o600

    for handler in logging.getLogger().handlers:
        handler.close()
