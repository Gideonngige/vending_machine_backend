"""Public probes return no database details and do not authenticate requests."""
import logging

from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import (
    BadMigrationError, CircularDependencyError, InconsistentMigrationHistory,
    InvalidMigrationPlan, NodeNotFoundError,
)
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


logger = logging.getLogger(__name__)


@never_cache
@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            return JsonResponse({"status": "not_ready"}, status=503)
    except (DatabaseError, BadMigrationError, CircularDependencyError,
            InconsistentMigrationHistory, InvalidMigrationPlan, NodeNotFoundError):
        logger.warning("Readiness database or migration check failed.")
        return JsonResponse({"status": "not_ready"}, status=503)
    return JsonResponse({"status": "ready"})
