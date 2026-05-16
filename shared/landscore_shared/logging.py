import logging
import structlog


def setup_logging(service_name: str, level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    structlog.contextvars.bind_contextvars(service=service_name)
