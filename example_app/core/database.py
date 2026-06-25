from pillar.db import Database
from pillar.di import container
from .config import settings

# Register the database singleton with the DI container once at import time.
# All repositories that declare `def __init__(self, db: Database)` will
# receive this instance automatically.
_db = Database(url=settings.database.url)
container.register_instance(Database, _db)
