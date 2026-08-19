"""Redis connection setup shared by caches/locks/queue infrastructure."""

from redis import Redis

from app.core.config import settings


redis_conn = None


def initialize_redis():
    """Initialize shared Redis connection."""
    global redis_conn

    try:
        redis_conn = Redis.from_url(
            settings.redis_url,
            decode_responses=False,
            socket_connect_timeout=10,
            socket_timeout=None,
            retry_on_timeout=True,
            socket_keepalive=True,
            socket_keepalive_options={},
            health_check_interval=30,
        )
        redis_conn.ping()
        print(f"Redis connected: {settings.redis_url}")
        return True
    except Exception as e:
        redis_conn = None
        print(f"Redis connection failed: {str(e)}")
        print(f"  URL: {settings.redis_url}")
        return False


def get_redis_connection():
    """Get Redis connection (cache/locks/runtime state)."""
    return redis_conn


# Legacy queue accessors are intentionally retained for backward-compatible
# imports while the runtime is Dramatiq-only.
def get_chat_interactions_queue():
    return None


def get_workitem_events_queue():
    return None


def get_chat_commands_queue():
    return None


def get_cortex_runs_queue():
    return None


def get_action_item_extraction_queue():
    return None


def close_redis():
    """Close Redis connection."""
    global redis_conn
    if redis_conn:
        redis_conn.close()
        print("Redis connection closed")
