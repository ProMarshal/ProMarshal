#!/bin/bash

DEFAULT_WORKER_QUEUES="cortex_runs chat_interactions workitem_events chat_commands action_item_extraction"
WORKER_QUEUES=${WORKER_QUEUES:-$DEFAULT_WORKER_QUEUES}
echo "Starting ProMarshal API with Dramatiq Worker..."
echo "Starting Dramatiq worker..."
python start_dramatiq_worker.py $WORKER_QUEUES &

# Start Neo4j Change Stream listener in background (if Neo4j is configured)
if [ -n "$NEO4J_URI" ]; then
    echo "Starting Neo4j Change Stream listener..."
    python -m app.graph.db_listener &
else
    echo "Skipping Neo4j listener (NEO4J_URI not set)"
fi

# Give workers a moment to start
sleep 2

# Start FastAPI
echo "Starting FastAPI server..."
uvicorn app.main:app --host 0.0.0.0 --port $PORT
