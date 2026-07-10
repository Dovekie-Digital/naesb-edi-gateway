import logging

import pytest

from app.logging_config import configure_logging


@pytest.mark.parametrize(
    "level_name,expected",
    [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    ],
)
def test_configure_logging_sets_correct_stdlib_level(level_name, expected):
    configure_logging(level=level_name, fmt="json")
    assert logging.getLogger().level == expected


def test_configure_logging_rejects_unknown_level():
    with pytest.raises(KeyError):
        configure_logging(level="NOT_A_REAL_LEVEL", fmt="json")
