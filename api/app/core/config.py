"""Application configuration using pydantic-settings"""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017/promarshal"
    brain_db_name: str = "brain"  # Brain database name (e.g., "brain" for prod, "brain-dev" for local)
    mongodb_max_pool_size: int = 50
    mongodb_min_pool_size: int = 10
    mongodb_max_idle_time_ms: int = 600000
    mongodb_server_selection_timeout_ms: int = 5000
    mongodb_connect_timeout_ms: int = 10000
    mongodb_socket_timeout_ms: int = 45000


    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = True

    # CORS
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Environment
    environment: str = "production"

    # Security
    encryption_key: str = ""
    jwt_secret_key: str = ""
    jwt_access_token_expire_minutes: int = 10080
    internal_service_secret: str = ""
    internal_service_secret_previous: str = ""
    internal_request_timestamp_skew_seconds: int = 60
    jwt_enforcement_enabled: bool = True
    cron_secret: str = ""  # Secret for GitHub Actions cron authentication

    # Slack OAuth
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""

    # Jira OAuth
    jira_client_id: str = ""
    jira_client_secret: str = ""
    jira_webhook_secret: str = ""  # Deprecated: Jira webhooks now use per-project URL tokens

    # URLs
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"

    # OpenAI (for AI features)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Groq (for LLM-generated task reminders)
    groq_api_key: str = ""

    # Anthropic (for Claude)
    anthropic_api_key: str = ""

    # Deepgram (for meeting audio transcription)
    deepgram_api_key: str = ""
    
    # Serper (for web search)
    serper_api_key: str = ""

    # Task-specific model config (can override via .env)
    pm_conversation_provider: str = "openai"  # For brainstorm/chat
    pm_conversation_model: str = "gpt-4o-mini"
    pm_reasoning_provider: str = "openai"  # For finalize check, conflict detection
    pm_reasoning_model: str = "gpt-4o"
    # Extraction and inference use conversation model

    # Canonical Cortex LLM config
    cortex_llm_primary_provider: str = ""
    cortex_llm_primary_model: str = ""
    cortex_llm_fallback_provider: str = "openai"
    cortex_llm_fallback_model: str = "gpt-4o-mini"
    cortex_llm_num_retries: int = 3
    cortex_llm_max_tokens: int = 2048
    cortex_llm_temperature: float = 0.3
    cortex_summary_model: str = ""
    cortex_gateway_shadow_compare_enabled: bool = False
    cortex_max_tool_iterations: int = 10
    cortex_op_ttl_seconds: int = 1800
    cortex_allow_redis_degraded: bool = False
    cortex_recent_turns_window: int = 10
    cortex_run_timeout: int = 60
    cortex_run_lock_ttl_seconds: int = 60
    cortex_run_lock_heartbeat_seconds: int = 15
    cortex_queue_drain_cap: int = 2
    cortex_inflight_ttl_seconds: int = 300
    cortex_inflight_stale_seconds: int = 45
    cortex_requeue_guard_ttl_seconds: int = 3600
    cortex_dispatch_ttl_seconds: int = 120
    cortex_task_read_fastpath_enabled: bool = True
    cortex_fastpath_compose_enabled: bool = True
    cortex_quality_engine_enabled: bool = True
    cortex_quality_semantic_enabled: bool = True
    cortex_quality_shadow_mode: bool = False
    cortex_quality_read_enabled: bool = True
    cortex_quality_read_shadow_mode: bool = False
    cortex_quality_read_fail_open_default: bool = True
    cortex_quality_policy_registry_enabled: bool = False
    cortex_quality_policy_artifact_path: str = "api/app/cortex/policies/quality-policy.v1.json"
    cortex_quality_policy_fail_fast: bool = False
    cortex_before_tool_call_wrapper_enabled: bool = True
    cortex_write_loop_guard_enabled: bool = True
    cortex_write_target_guard_enabled: bool = True
    cortex_max_write_tool_calls_per_turn: int = 5
    cortex_max_total_tool_calls_per_turn: int = 10
    cortex_max_turn_runtime_seconds: int = 25
    cortex_max_repair_attempts_per_turn: int = 1
    cortex_proposal_field_meta_min_confidence: float = 0.6
    cortex_read_source_mode: str = "brain_first"
    cortex_plan_min_actions: int = 2
    cortex_plan_confidence_threshold: float = 0.72
    cortex_plan_max_steps_per_turn: int = 6
    cortex_pending_plan_ttl_seconds: int = 1800
    cortex_pending_plan_override_on_new_action: bool = True
    cortex_pending_plan_auto_resume_on_same_intent: bool = True
    cortex_jira_auto_sprint_enabled: bool = True
    jira_no_active_sprint_cache_ttl_seconds: int = 120
    promarshal_prompt_registry_enabled: bool = True
    work_item_parent_ranking_llm_timeout_seconds: int = 20
    promarshal_global_scope_policy_enabled: bool = True
    scope_two_stage_enabled: bool = True
    scope_llm_classifier_enabled: bool = True
    team_poll_response_composer_enabled: bool = True
    action_items_enabled: bool = True
    action_items_auto_detect_enabled: bool = True
    action_items_digest_enabled: bool = True
    action_items_ingest_dedupe_window_hours: int = 168
    action_items_followup_actor_reminder_hours: int = 48
    action_items_followup_pm_escalation_hours: int = 4
    action_items_title_rewrite_enabled: bool = True
    action_items_title_rewrite_min_confidence: float = 0.68
    action_items_title_max_chars: int = 180
    action_items_title_rewrite_max_retries: int = 2
    action_items_title_rewrite_backoff_ms: str = "300,900"
    action_items_title_rewrite_retry_jitter_ms: int = 120
    action_items_comment_classifier_enabled: bool = True
    action_items_comment_classifier_min_confidence: float = 0.75
    action_item_pending_relevance_enabled: bool = True
    action_item_pending_relevance_llm_model: str = "gpt-4o-mini"
    action_item_pending_relevance_llm_fallback_enabled: bool = True
    action_item_pending_relevance_llm_min_confidence: float = 0.65
    action_item_pending_relevance_llm_timeout_seconds: int = 8

    # Cadence session/concurrency policy
    cadence_session_ttl_seconds: int = 7200
    cadence_single_active_scope: str = "global"  # project | global
    cadence_cron_lock_enabled: bool = True
    cadence_cron_lock_ttl_seconds: int = 1800
    cadence_interaction_idempotency_ttl_seconds: int = 300
    cadence_backlog_prompt_max_active_tasks: int = 1
    cadence_lease_enforce: bool = True
    cadence_dm_lease_enabled: bool = True
    cadence_modal_lease_enabled: bool = True
    cadence_dm_queue_canary_pct: int = 100
    cadence_cosmetic_llm_enabled: bool = True
    cadence_cosmetic_llm_queue_depth_threshold: int = 50
    cadence_lease_ttl_seconds: int = 45
    cadence_lease_heartbeat_seconds: int = 15
    cadence_lease_retry_delay_ms: int = 2000

    # Session storage/isolation flags
    session_strict_project_binding_enabled: bool = False
    session_actor_authorization_enabled: bool = False
    session_cleanup_index_grace_minutes: int = 60
    session_cleanup_cadence_index_grace_minutes: int = 240
    session_cleanup_cadence_retention_days: int = 7

    # Central scheduler controls
    scheduler_lease_ttl_seconds: int = 120
    scheduler_max_due_per_tick: int = 100
    scheduler_retry_max_attempts: int = 3
    scheduler_retry_backoff_seconds: int = 30
    scheduler_stale_run_timeout_seconds: int = 600
    scheduler_use_dispatcher: bool = True
    scheduler_dispatcher_daily_interval_minutes: int = 15
    scheduler_dispatcher_fast_lane_budget: int = 70
    scheduler_dispatcher_daily_lane_budget: int = 30
    scheduler_dispatcher_seed_interval_minutes: int = 10
    scheduler_dispatcher_tick_lock_ttl_seconds: int = 55
    scheduler_job_type_limits_json: str = ""
    schedule_runs_retention_days: int = 2
    channel_index_retention_days: int = 30
    cadence_daily_summary_retention_days: int = 90
    cleanup_summary_retention_enabled: bool = True
    cleanup_job_batch_limit: int = 10000
    scheduler_runner_enabled: bool = False
    pm_board_summary_cache_ttl_seconds: int = 1800
    pm_board_summary_dirty_ttl_seconds: int = 1800
    pm_board_summary_job_status_ttl_seconds: int = 1800
    pm_board_summary_job_lock_ttl_seconds: int = 120
    planner_cache_enabled: bool = True
    planner_status_cache_ttl_seconds: int = 60
    planner_entities_cache_ttl_seconds: int = 60
    planner_current_stage_cache_ttl_seconds: int = 30
    planner_bootstrap_async_enabled: bool = True
    planner_bootstrap_payload_ttl_seconds: int = 86400
    planner_bootstrap_job_status_ttl_seconds: int = 1800
    planner_bootstrap_job_lock_ttl_seconds: int = 120
    recommendation_runtime_ttl_seconds: int = 21600
    recommendation_payload_cache_ttl_seconds: int = 90
    recommendation_debounce_window_seconds: int = 120
    recommendation_dirty_ttl_seconds: int = 1800
    recommendation_incremental_interval_seconds: int = 300
    recommendation_incremental_batch_limit: int = 200
    recommendation_full_daily_times: str = "09:00,18:00"
    recommendation_overload_threshold: float = 70.0
    recommendation_due_soon_hours: int = 48
    recommendation_sprint_end_soon_hours: int = 72
    recommendation_sprint_open_critical_threshold: int = 2
    recommendation_project_type_thresholds_json: str = ""
    recommendation_workspace_thresholds_json: str = ""

    # Team poll defaults
    team_poll_enabled: bool = True
    team_poll_default_deadline_minutes: int = 90
    team_poll_eod_hour_local: int = 17
    team_poll_nudge_offsets_minutes: str = "30,60"
    team_poll_max_audience: int = 200
    team_poll_min_response_window_minutes: int = 30
    team_poll_delivery_extension_cap_minutes: int = 60
    team_poll_worker_parallelism: int = 4
    team_poll_delivery_batch_size: int = 500
    team_poll_nudge_batch_size: int = 500
    team_poll_max_pages_per_cron_run: int = 20
    team_poll_max_retries: int = 5
    team_poll_retry_backoff_seconds: str = "5,15,30,60,120"
    team_poll_workspace_rate_limit_per_minute: int = 120
    team_poll_global_rate_limit_per_minute: int = 1000
    team_poll_cycle_interval_minutes: int = 5
    team_poll_session_terminal_grace_minutes: int = 60
    team_poll_result_retention_days: int = 30
    team_poll_llm_response_enabled: bool = True
    team_poll_question_rewrite_enabled: bool = True
    team_poll_question_rewrite_min_confidence: float = 0.7
    team_poll_question_rewrite_max_chars: int = 220
    team_poll_question_rewrite_max_retries: int = 2
    team_poll_question_rewrite_backoff_ms: str = "300,900"
    team_poll_question_rewrite_retry_jitter_ms: int = 150
    team_poll_owner_free_text_via_cortex: bool = True
    team_poll_relevance_enabled: bool = True
    team_poll_relevance_model: str = "text-embedding-3-small"
    team_poll_relevance_threshold: float = 0.62
    team_poll_relevance_gray_band: float = 0.08
    team_poll_relevance_cache_ttl_seconds: int = 3600
    team_poll_relevance_tiebreak_enabled: bool = False
    team_poll_relevance_llm_fallback_enabled: bool = True
    team_poll_relevance_llm_min_confidence: float = 0.65
    team_poll_post_cadence_reminder_enabled: bool = True
    team_poll_post_cadence_reminder_cooldown_seconds: int = 900

    cortex_agents_tracing_enabled: bool = False

    # Redis (for queues and caching)
    redis_url: str = "redis://localhost:6379"

    # Neo4j (for graph database)
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""

    # Email provider configuration
    email_provider: str = "zeptomail"
    zeptomail_api_url: str = ""
    zeptomail_api_key: str = ""
    sendgrid_api_key: str = ""
    email_from: str = "ProMarshal <noreply@promarshal.ai>"

    @model_validator(mode="after")
    def apply_cortex_defaults(self) -> "Settings":
        """Backfill canonical Cortex fields from conversation-model defaults."""
        if not (self.cortex_llm_primary_provider or "").strip():
            for candidate in (
                (self.pm_conversation_provider or "").strip(),
                "openai",
            ):
                if candidate:
                    self.cortex_llm_primary_provider = candidate
                    break

        if not (self.cortex_llm_primary_model or "").strip():
            for candidate in (
                self.pm_conversation_model,
                self.openai_model,
            ):
                cleaned = (candidate or "").strip()
                if cleaned:
                    self.cortex_llm_primary_model = cleaned
                    break
            else:
                self.cortex_llm_primary_model = "gpt-4o-mini"

        if not (self.cortex_summary_model or "").strip():
            self.cortex_summary_model = self.cortex_llm_primary_model

        # Direct-enable global active-session scope for Cadence.
        self.cadence_single_active_scope = "global"
        self.cadence_session_ttl_seconds = max(300, int(self.cadence_session_ttl_seconds or 7200))
        self.cadence_backlog_prompt_max_active_tasks = max(
            0,
            int(self.cadence_backlog_prompt_max_active_tasks if self.cadence_backlog_prompt_max_active_tasks is not None else 1),
        )
        self.cadence_dm_queue_canary_pct = max(
            0,
            min(100, int(self.cadence_dm_queue_canary_pct if self.cadence_dm_queue_canary_pct is not None else 100)),
        )
        self.cadence_cosmetic_llm_queue_depth_threshold = max(
            1,
            int(
                self.cadence_cosmetic_llm_queue_depth_threshold
                if self.cadence_cosmetic_llm_queue_depth_threshold is not None
                else 50
            ),
        )
        self.cadence_lease_ttl_seconds = max(5, int(self.cadence_lease_ttl_seconds or 45))
        self.cadence_lease_heartbeat_seconds = max(
            2,
            min(
                int(self.cadence_lease_heartbeat_seconds or 15),
                max(2, self.cadence_lease_ttl_seconds - 1),
            ),
        )
        self.cadence_lease_retry_delay_ms = max(0, int(self.cadence_lease_retry_delay_ms or 2000))
        self.cortex_run_lock_ttl_seconds = max(30, int(self.cortex_run_lock_ttl_seconds or 60))
        self.cortex_run_lock_heartbeat_seconds = max(
            5,
            min(
                int(self.cortex_run_lock_heartbeat_seconds or 15),
                max(5, self.cortex_run_lock_ttl_seconds - 5),
            ),
        )
        self.cortex_queue_drain_cap = max(1, int(self.cortex_queue_drain_cap or 2))
        self.cortex_inflight_ttl_seconds = max(60, int(self.cortex_inflight_ttl_seconds or 300))
        self.cortex_inflight_stale_seconds = max(15, int(self.cortex_inflight_stale_seconds or 45))
        self.cortex_requeue_guard_ttl_seconds = max(
            self.cortex_inflight_ttl_seconds,
            int(self.cortex_requeue_guard_ttl_seconds or 3600),
        )
        self.cortex_dispatch_ttl_seconds = max(30, int(self.cortex_dispatch_ttl_seconds or 120))
        self.cortex_max_write_tool_calls_per_turn = max(
            1, int(self.cortex_max_write_tool_calls_per_turn or 5)
        )
        self.cortex_max_total_tool_calls_per_turn = max(
            self.cortex_max_write_tool_calls_per_turn,
            int(self.cortex_max_total_tool_calls_per_turn or 10),
        )
        self.cortex_max_turn_runtime_seconds = max(
            1, int(self.cortex_max_turn_runtime_seconds or 25)
        )
        self.cortex_max_repair_attempts_per_turn = max(
            0, int(self.cortex_max_repair_attempts_per_turn if self.cortex_max_repair_attempts_per_turn is not None else 1)
        )
        self.jira_no_active_sprint_cache_ttl_seconds = max(
            10, int(self.jira_no_active_sprint_cache_ttl_seconds or 120)
        )
        self.cortex_proposal_field_meta_min_confidence = max(
            0.0,
            min(
                1.0,
                float(
                    self.cortex_proposal_field_meta_min_confidence
                    if self.cortex_proposal_field_meta_min_confidence is not None
                    else 0.6
                ),
            ),
        )
        self.session_cleanup_index_grace_minutes = max(
            5, int(self.session_cleanup_index_grace_minutes or 60)
        )
        self.session_cleanup_cadence_index_grace_minutes = max(
            5, int(self.session_cleanup_cadence_index_grace_minutes or 240)
        )
        self.session_cleanup_cadence_retention_days = max(
            1, int(self.session_cleanup_cadence_retention_days or 7)
        )
        self.scheduler_lease_ttl_seconds = max(15, int(self.scheduler_lease_ttl_seconds or 120))
        self.scheduler_max_due_per_tick = max(1, int(self.scheduler_max_due_per_tick or 100))
        self.scheduler_retry_max_attempts = max(1, int(self.scheduler_retry_max_attempts or 3))
        self.scheduler_retry_backoff_seconds = max(1, int(self.scheduler_retry_backoff_seconds or 30))
        self.scheduler_stale_run_timeout_seconds = max(30, int(self.scheduler_stale_run_timeout_seconds or 600))
        self.scheduler_dispatcher_daily_interval_minutes = max(
            1, int(self.scheduler_dispatcher_daily_interval_minutes or 15)
        )
        self.scheduler_dispatcher_fast_lane_budget = max(
            1, int(self.scheduler_dispatcher_fast_lane_budget or 70)
        )
        self.scheduler_dispatcher_daily_lane_budget = max(
            1, int(self.scheduler_dispatcher_daily_lane_budget or 30)
        )
        self.scheduler_dispatcher_seed_interval_minutes = max(
            1, int(self.scheduler_dispatcher_seed_interval_minutes or 10)
        )
        self.scheduler_dispatcher_tick_lock_ttl_seconds = max(
            5, int(self.scheduler_dispatcher_tick_lock_ttl_seconds or 55)
        )
        self.schedule_runs_retention_days = max(
            1, int(self.schedule_runs_retention_days or 2)
        )
        self.channel_index_retention_days = max(
            1, int(self.channel_index_retention_days or 30)
        )
        self.cadence_daily_summary_retention_days = max(
            1, int(self.cadence_daily_summary_retention_days or 90)
        )
        self.cleanup_job_batch_limit = max(
            1, int(self.cleanup_job_batch_limit or 10000)
        )
        self.mongodb_max_pool_size = max(1, int(self.mongodb_max_pool_size or 50))
        self.mongodb_min_pool_size = max(
            0,
            min(
                int(self.mongodb_min_pool_size if self.mongodb_min_pool_size is not None else 10),
                self.mongodb_max_pool_size,
            ),
        )
        self.mongodb_max_idle_time_ms = max(1000, int(self.mongodb_max_idle_time_ms or 600000))
        self.mongodb_server_selection_timeout_ms = max(
            1000, int(self.mongodb_server_selection_timeout_ms or 5000)
        )
        self.mongodb_connect_timeout_ms = max(1000, int(self.mongodb_connect_timeout_ms or 10000))
        self.mongodb_socket_timeout_ms = max(1000, int(self.mongodb_socket_timeout_ms or 45000))
        self.team_poll_default_deadline_minutes = max(
            15, int(self.team_poll_default_deadline_minutes or 90)
        )
        self.team_poll_eod_hour_local = max(
            0, min(23, int(self.team_poll_eod_hour_local if self.team_poll_eod_hour_local is not None else 17))
        )
        self.team_poll_max_audience = max(1, int(self.team_poll_max_audience or 200))
        self.team_poll_min_response_window_minutes = max(
            5, int(self.team_poll_min_response_window_minutes or 30)
        )
        self.team_poll_delivery_extension_cap_minutes = max(
            0, int(self.team_poll_delivery_extension_cap_minutes or 60)
        )
        self.team_poll_worker_parallelism = max(1, int(self.team_poll_worker_parallelism or 4))
        self.team_poll_delivery_batch_size = max(1, int(self.team_poll_delivery_batch_size or 500))
        self.team_poll_nudge_batch_size = max(1, int(self.team_poll_nudge_batch_size or 500))
        self.team_poll_max_pages_per_cron_run = max(1, int(self.team_poll_max_pages_per_cron_run or 20))
        self.team_poll_max_retries = max(0, int(self.team_poll_max_retries or 5))
        self.team_poll_workspace_rate_limit_per_minute = max(
            1, int(self.team_poll_workspace_rate_limit_per_minute or 120)
        )
        self.team_poll_global_rate_limit_per_minute = max(
            1, int(self.team_poll_global_rate_limit_per_minute or 1000)
        )
        self.team_poll_cycle_interval_minutes = max(1, int(self.team_poll_cycle_interval_minutes or 5))
        self.team_poll_session_terminal_grace_minutes = max(
            5, int(self.team_poll_session_terminal_grace_minutes or 60)
        )
        self.team_poll_result_retention_days = max(
            1, int(self.team_poll_result_retention_days or 30)
        )
        self.team_poll_question_rewrite_min_confidence = max(
            0.0,
            min(1.0, float(self.team_poll_question_rewrite_min_confidence if self.team_poll_question_rewrite_min_confidence is not None else 0.7)),
        )
        self.team_poll_question_rewrite_max_chars = max(
            40, int(self.team_poll_question_rewrite_max_chars or 220)
        )
        self.team_poll_question_rewrite_max_retries = max(
            0, int(self.team_poll_question_rewrite_max_retries or 2)
        )
        self.team_poll_question_rewrite_retry_jitter_ms = max(
            0, int(self.team_poll_question_rewrite_retry_jitter_ms or 150)
        )
        self.action_items_title_rewrite_min_confidence = max(
            0.0,
            min(
                1.0,
                float(
                    self.action_items_title_rewrite_min_confidence
                    if self.action_items_title_rewrite_min_confidence is not None
                    else 0.68
                ),
            ),
        )
        self.action_items_title_max_chars = max(
            60, int(self.action_items_title_max_chars or 180)
        )
        self.action_items_title_rewrite_max_retries = max(
            0, int(self.action_items_title_rewrite_max_retries or 2)
        )
        self.action_items_title_rewrite_retry_jitter_ms = max(
            0, int(self.action_items_title_rewrite_retry_jitter_ms or 120)
        )
        self.action_items_comment_classifier_min_confidence = max(
            0.0,
            min(
                1.0,
                float(
                    self.action_items_comment_classifier_min_confidence
                    if self.action_items_comment_classifier_min_confidence is not None
                    else 0.75
                ),
            ),
        )
        self.pm_board_summary_cache_ttl_seconds = max(
            5, int(self.pm_board_summary_cache_ttl_seconds or 1800)
        )
        self.pm_board_summary_dirty_ttl_seconds = max(
            60, int(self.pm_board_summary_dirty_ttl_seconds or 1800)
        )
        self.pm_board_summary_job_status_ttl_seconds = max(
            300, int(self.pm_board_summary_job_status_ttl_seconds or 1800)
        )
        self.pm_board_summary_job_lock_ttl_seconds = max(
            30, int(self.pm_board_summary_job_lock_ttl_seconds or 120)
        )
        self.planner_status_cache_ttl_seconds = max(
            5, int(self.planner_status_cache_ttl_seconds or 60)
        )
        self.planner_entities_cache_ttl_seconds = max(
            5, int(self.planner_entities_cache_ttl_seconds or 60)
        )
        self.planner_current_stage_cache_ttl_seconds = max(
            5, int(self.planner_current_stage_cache_ttl_seconds or 30)
        )
        self.planner_bootstrap_payload_ttl_seconds = max(
            30, int(self.planner_bootstrap_payload_ttl_seconds or 300)
        )
        self.planner_bootstrap_job_status_ttl_seconds = max(
            60, int(self.planner_bootstrap_job_status_ttl_seconds or 1800)
        )
        self.planner_bootstrap_job_lock_ttl_seconds = max(
            30, int(self.planner_bootstrap_job_lock_ttl_seconds or 120)
        )
        self.recommendation_runtime_ttl_seconds = max(
            60, int(self.recommendation_runtime_ttl_seconds or 21600)
        )
        self.recommendation_payload_cache_ttl_seconds = max(
            60, min(120, int(self.recommendation_payload_cache_ttl_seconds or 90))
        )
        self.recommendation_debounce_window_seconds = max(
            30, int(self.recommendation_debounce_window_seconds or 120)
        )
        self.recommendation_dirty_ttl_seconds = max(
            30, int(self.recommendation_dirty_ttl_seconds or 1800)
        )
        self.recommendation_incremental_interval_seconds = max(
            30, int(self.recommendation_incremental_interval_seconds or 300)
        )
        self.recommendation_incremental_batch_limit = max(
            1, int(self.recommendation_incremental_batch_limit or 200)
        )
        self.recommendation_overload_threshold = max(
            0.0, min(100.0, float(self.recommendation_overload_threshold if self.recommendation_overload_threshold is not None else 70.0))
        )
        self.recommendation_due_soon_hours = max(
            1, int(self.recommendation_due_soon_hours or 48)
        )
        self.recommendation_sprint_end_soon_hours = max(
            1, int(self.recommendation_sprint_end_soon_hours or 72)
        )
        self.recommendation_sprint_open_critical_threshold = max(
            1, int(self.recommendation_sprint_open_critical_threshold or 2)
        )
        return self

    @property
    def cors_origins(self) -> List[str]:
        """Parse comma-separated origins into list"""
        return [origin.strip() for origin in self.allowed_origins.split(",")]


settings = Settings()
