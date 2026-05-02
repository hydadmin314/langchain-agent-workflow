from loguru import logger
import sys

logger.remove()
logger.add(
    sys.stderr,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)

__all__ = ["logger"]