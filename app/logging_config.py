import logging
import logging.handlers
from pathlib import Path

import structlog

# logging.getLevelName() is bidirectional (name -> int AND int -> name) as a
# historical stdlib quirk that the docs explicitly warn not to rely on --
# spell the mapping out explicitly instead.
_LEVEL_NAME_TO_INT = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# Uvicorn installs its own handlers directly on these loggers with
# propagate=False, using a formatter with no timestamp field -- bypassing
# whatever the app configures on the root logger. Stripping their handlers
# and re-enabling propagation routes them through the same pipeline as
# app-level structlog calls below.
_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(level: str = "INFO", fmt: str = "json", directory: str | None = None) -> None:
    level_int = _LEVEL_NAME_TO_INT[level.upper()]

    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer()
    )

    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if directory is not None:
        Path(directory).mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                Path(directory) / "app.log", maxBytes=10_000_000, backupCount=5
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = handlers
    root_logger.setLevel(level_int)

    for name in _UVICORN_LOGGER_NAMES:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
