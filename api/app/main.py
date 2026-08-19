"""ProMarshal API - Main application entry point"""

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.database import connect_to_mongo, close_mongo_connection, ensure_mongo_ready, get_database
from app.core.redis_queue import initialize_redis, close_redis, get_redis_connection
from app.cadence.llm_service import initialize_llm_service
from app.cortex.quality.policy_registry import validate_quality_policy_startup
from app.domain.capabilities.startup import install_builtin_bundles
from app.integrations.base.webhook_registry import register_webhook_verifier
from app.integrations.jira.webhook_verifier import JiraWebhookVerifier
from app.integrations.slack.webhook_verifier import SlackWebhookVerifier

# Import routers from domain modules
from app.auth import router as auth_router
from app.users import router as users_router
from app.projects import router as projects_router
from app.action_items.router import router as action_items_router
from app.integrations.router import router as integrations_router
from app.integrations.slack.commands_router import router as slack_commands_router
from app.integrations.slack.interactions_router import router as slack_interactions_router
from app.integrations.meetings.router import router as meetings_router
from app.integrations.plugin.router import router as plugin_router
from app.routes.jira_webhooks import router as jira_webhooks_router  # TEST
from app.routes.cron import router as cron_router
from app.routes.work_item_migration import router as work_item_migration_router
from app.workers.slack_messages.router import router as slack_messages_router
from app.planner.router import router as planner_router
from app.planner.agent_router import router as agent_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    install_builtin_bundles()
    validate_quality_policy_startup()
    register_webhook_verifier("slack", SlackWebhookVerifier())
    register_webhook_verifier("jira", JiraWebhookVerifier())
    await connect_to_mongo()

    # Initialize Redis for queues and caching
    initialize_redis()

    # Initialize Groq LLM service if API key is configured
    if settings.groq_api_key and settings.groq_api_key != "your-groq-api-key-here":
        initialize_llm_service(settings.groq_api_key)
    else:
        print("Groq API key not configured - LLM messages will use fallback templates")

    yield
    # Shutdown
    close_redis()
    await close_mongo_connection()


_docs_enabled = str(getattr(settings, "environment", "") or "").strip().lower() != "production"

app = FastAPI(
    title="ProMarshal API",
    description="AI-powered project management assistant backend",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers from domain modules
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(projects_router)
app.include_router(action_items_router)
app.include_router(integrations_router)
app.include_router(slack_commands_router)
app.include_router(slack_interactions_router)
app.include_router(jira_webhooks_router)  # TEST
app.include_router(cron_router)
app.include_router(work_item_migration_router)
app.include_router(slack_messages_router)
app.include_router(planner_router)
app.include_router(agent_router)
app.include_router(meetings_router)
app.include_router(plugin_router)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Welcome to ProMarshal API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.get("/readyz")
async def readiness_check():
    """Readiness probe - checks MongoDB and Redis connectivity."""
    errors = {}

    try:
        await ensure_mongo_ready()
        db = get_database()
        await db.command("ping")
    except Exception as exc:
        errors["mongodb"] = str(exc)

    try:
        redis_conn = get_redis_connection()
        if redis_conn is None:
            raise RuntimeError("Redis connection unavailable")
        redis_conn.ping()
    except Exception as exc:
        errors["redis"] = str(exc)

    if errors:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "errors": errors},
        )

    return {"status": "ready"}
