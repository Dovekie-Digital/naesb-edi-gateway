import logging

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


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    level_int = _LEVEL_NAME_TO_INT[level.upper()]
    # force=True: logging.basicConfig() is a no-op if the root logger already
    # has handlers (e.g. uvicorn, pytest, or another library configured
    # logging first) -- without it, this function can silently fail to apply
    # `level` at all.
    logging.basicConfig(level=level_int, format="%(message)s", force=True)

    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
