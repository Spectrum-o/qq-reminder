from .assignment_service import sync_assignments_from_json
from .recurring_service import sync_recurring_assignments


async def sync_assignment_sources() -> None:
    await sync_assignments_from_json()
    await sync_recurring_assignments()
