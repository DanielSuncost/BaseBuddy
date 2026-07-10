"""
Core business logic services.

Services contain the business logic for core surveillance operations.
They are designed to be reusable across different API endpoints and frontends.
"""
from .archive_service import ArchiveService
from .backup_service import BackupManager
from .health_service import HealthMonitor
from .retention_service import RetentionService
from .storage_service import StorageService

__all__ = [
    'ArchiveService',
    'BackupManager',
    'HealthMonitor',
    'RetentionService',
    'StorageService',
]


def initialize_services(app):
    """
    Initialize all core services with app context.
    
    Args:
        app: Flask application instance
    """
    # Services will be initialized as needed by routes
    # This function will be used for any services that need app-level initialization
    pass
