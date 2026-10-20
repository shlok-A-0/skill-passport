import os

import dotenv

os.environ.setdefault("APP_ENV", "development")
# Tests read configuration from the process environment only, never a developer's backend/.env
# (as in CI), so results don't depend on local settings or real API keys. Set before app.config
# is imported, which binds load_dotenv at import time.
dotenv.load_dotenv = lambda *args, **kwargs: False
