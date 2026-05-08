import logging
import sys

from pythonjsonlogger import jsonlogger


def setup_logger():
    """Configure single-line JSON structured logging for Promtail → Loki."""
    logger = logging.getLogger("compliance")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)

    # Note: python-json-logger==2.0.7 has a bug where rename_fields with
    # identity mappings (e.g. {"asctime": "asctime"}) strips those fields
    # from the output. Omit rename_fields entirely so asctime/levelname
    # appear in the JSON payload as Promtail/Loki expect.
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        json_ensure_ascii=False,
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = setup_logger()
