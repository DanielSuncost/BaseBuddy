"""
Logging configuration for BaseBuddy.

Provides centralized logging setup with rotating file handlers,
error tracking, and system exception handling.
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler

from basebuddy.core.paths import get_repo_root


def setup_logging(log_dir=None, log_level='INFO'):
    """
    Configure application logging with file handlers and formatters.
    
    Args:
        log_dir: Directory for log files (defaults to './logs')
        log_level: Logging level ('DEBUG', 'INFO', 'WARNING', 'ERROR')
        
    Returns:
        Configured logger instance
    """
    if log_dir is None:
        log_dir = os.path.join(get_repo_root(), 'logs')
    
    os.makedirs(log_dir, exist_ok=True)
    
    app_logger = logging.getLogger('basebuddy')
    app_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Clear existing handlers to avoid duplicates
    app_logger.handlers.clear()
    
    # Rotating file handler for all logs
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'basebuddy.log'),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    app_logger.addHandler(file_handler)
    
    # Error-only file handler
    error_handler = RotatingFileHandler(
        os.path.join(log_dir, 'errors.log'),
        maxBytes=10 * 1024 * 1024,
        backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    app_logger.addHandler(error_handler)
    
    # Console handler for critical errors
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    app_logger.addHandler(console_handler)
    
    # Suppress Flask GET request logs
    werkzeug_log = logging.getLogger('werkzeug')
    werkzeug_log.setLevel(logging.ERROR)
    
    # Log startup info
    import psutil
    app_logger.info("=" * 60)
    app_logger.info("BaseBuddy starting up")
    app_logger.info(f"Logs directory: {log_dir}")
    app_logger.info(f"Python: {sys.version}")
    app_logger.info(f"CPU cores: {psutil.cpu_count()}")
    app_logger.info(f"Memory: {psutil.virtual_memory().total / (1024**3):.2f}GB")
    app_logger.info("=" * 60)
    
    return app_logger


def setup_exception_handler(logger):
    """
    Install global exception handler to log uncaught exceptions.
    
    Args:
        logger: Logger instance to use for exception logging
    """
    def log_exception(exc_type, exc_value, exc_traceback):
        """Log uncaught exceptions."""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    
    sys.excepthook = log_exception


# Global logger instance (initialized by setup_logging)
logger = logging.getLogger('basebuddy')


