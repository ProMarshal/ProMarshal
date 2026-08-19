#!/usr/bin/env bash
set -euo pipefail

DEFAULT_QUEUES="cortex_runs chat_interactions workitem_events chat_commands action_item_extraction"
QUEUES="${WORKER_QUEUES:-$DEFAULT_QUEUES}"

# Start Neo4j Change Stream listener in background if Neo4j is configured
if [ -n "${NEO4J_URI:-}" ]; then
    echo "Starting Neo4j Change Stream listener..."
    python -m app.graph.db_listener &
else
    echo "Skipping Neo4j listener (NEO4J_URI not set)"
fi

echo "Starting Dramatiq worker with queues: $QUEUES"
exec python start_dramatiq_worker.py $QUEUES
