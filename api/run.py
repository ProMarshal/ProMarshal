import logging
import uvicorn
import os
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

if __name__ == "__main__":
    # Render provides the port via the PORT env var; fall back to configured default locally.
    port = int(os.environ.get("PORT", settings.api_port))
    host = os.environ.get("HOST", settings.api_host)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=settings.api_reload,
        log_level="info",
    )
