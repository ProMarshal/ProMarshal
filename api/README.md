# ProMarshal API

Python FastAPI backend for ProMarshal - AI-powered project management assistant.

## Setup

### 1. Install Dependencies

```bash
cd api
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Update the MongoDB URI in `.env`:
```
MONGODB_URI=mongodb://localhost:27017/promarshal
```

### 3. Start MongoDB

Make sure MongoDB is running locally or update the URI to use MongoDB Atlas.

### 4. Start Redis

```bash
# Using Docker (recommended)
docker run -d -p 6379:6379 redis:latest

# Or install Redis locally
# Mac: brew install redis && brew services start redis
# Ubuntu: sudo apt install redis-server && sudo systemctl start redis
```

Update `.env` if using non-default Redis:
```
REDIS_URL=redis://localhost:6379
```

### 5. Start Worker (Background Jobs)

Open a new terminal:

```bash
cd api
source venv/bin/activate

# Dramatiq runtime
DRAMATIQ_WORKER_PROCESSES=1 DRAMATIQ_WORKER_THREADS=8 \
python start_dramatiq_worker.py cortex_runs chat_interactions workitem_events chat_commands action_item_extraction
```

Keep this terminal running for background job processing.

### 6. Run the Server

```bash
python run.py
```

Or using uvicorn directly:
```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`

## API Documentation

Once the server is running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Parsing Hardening Runtime

OpenClaw-inspired parsing hardening is now always-on in runtime orchestration paths.

## Collections

### Users Collection (`users`)
```javascript
{
  _id: ObjectId,
  user_id: "shiva",  // Extracted from email
  name: "Shiva Kumar",
  email: "shiva@gmail.com",  // Unique
  created_at: ISODate,
  updated_at: ISODate
}
```

### Projects Collection (`projects`)
```javascript
{
  _id: ObjectId,
  project_name: "Website Redesign",
  user_id: "shiva",  // Reference to users.user_id
  tier: "free" | "paid",
  timezone: "Asia/Kolkata",
  reminder_config: {
    enabled: true,
    time: "09:00",  // HH:MM format
    timezone: "Asia/Kolkata"
  },
  members: [
    {
      user_id: "shiva",
      email: "shiva@gmail.com",
      role: "owner" | "admin" | "member",
      integration_ids: {
        jira_account_id: "5b7d744...",
        slack_user_id: "U123ABC"
      }
    }
  ],
  integrations: {
    slack: {
      team_id: "T12345",
      team_name: "My Workspace",
      bot_user_id: "U123ABC",
      access_token: "encrypted...",  // Encrypted
      status: "connected",
      connected_at: ISODate
    },
    jira: {
      cloud_id: "abc123",
      site_url: "mycompany.atlassian.net",
      access_token: "encrypted...",  // Encrypted
      refresh_token: "encrypted...",  // Encrypted
      status: "connected",
      connected_at: ISODate
    }
  },
  created_at: ISODate,
  updated_at: ISODate
}
```

### Tasks Collection (`tasks`)
```javascript
{
  _id: ObjectId,
  project_id: ObjectId,  // Reference to projects._id
  integration_type: "jira",
  external_id: "PROJ-123",  // Jira task key
  title: "Fix authentication bug",
  status: "in_progress",
  assignee_account_id: "5b7d7440...",  // Jira account ID
  assignee_email: "user@example.com",
  assignee_name: "User Name",
  priority: "high" | "medium" | "low",
  due_date: ISODate,
  url: "https://mycompany.atlassian.net/browse/PROJ-123",
  comments: [],
  raw_data: {},  // Full Jira response
  synced_at: ISODate,
  created_at: ISODate,
  updated_at: ISODate
}
```

### Slack Sessions Collection (`slack_sessions`) - Paid Tier Only
```javascript
{
  _id: ObjectId,
  session_id: "uuid",
  user_id: "member1",
  slack_user_id: "U123ABC",
  project_id: ObjectId,
  status: "awaiting_status" | "awaiting_comment" | "complete",
  tasks: [
    {
      task_key: "PROJ-123",
      title: "Fix bug",
      current_status: "in_progress",
      updated_status: "done",
      comment: "Fixed auth flow",
      jira_updated: true
    }
  ],
  current_task_index: 0,
  correlation_id: "cron-uuid",
  created_at: ISODate,
  expires_at: ISODate  // 24 hours
}
```

## API Endpoints

### Users
- `POST /api/users/` - Create a new user
- `GET /api/users/{user_id}` - Get user by ID
- `GET /api/users/email/{email}` - Get user by email
- `GET /api/users/` - List all users (supports pagination)
- `PUT /api/users/{user_id}` - Update user
- `DELETE /api/users/{user_id}` - Delete user

### Projects
- `POST /api/projects/` - Create a new project
- `GET /api/projects/{project_id}` - Get project by ID
- `GET /api/projects/` - List projects (supports filtering by user_id and pagination)
- `PUT /api/projects/{project_id}` - Update project
- `DELETE /api/projects/{project_id}` - Delete project
- `POST /api/projects/{project_id}/invites` - Invite members to project
- `POST /api/projects/invites/{invite_token}/accept` - Accept project invite

### Integrations - Slack
- `GET /api/integrations/slack/connect` - Initiate Slack OAuth flow
- `GET /api/integrations/slack/callback` - Handle Slack OAuth callback
- `DELETE /api/integrations/slack/disconnect` - Disconnect Slack integration
- `POST /api/integrations/slack/events` - Receive Slack events (bot mentions)
- `POST /api/integrations/slack/interactions` - **Canonical** Slack interactive components endpoint

### Integrations - Jira
- `GET /api/integrations/jira/connect` - Initiate Jira OAuth flow
- `GET /api/integrations/jira/callback` - Handle Jira OAuth callback
- `DELETE /api/integrations/jira/disconnect` - Disconnect Jira integration
- `POST /api/integrations/jira/sync` - Manually trigger Jira task sync

### Jira Webhooks
- `POST /api/jira/webhooks/task-updated` - Receive Jira webhook events
  - Task created
  - Task updated
  - Task deleted

## Example Usage

### Create a User
```bash
curl -X POST "http://localhost:8000/api/users/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John Doe",
    "email": "john.doe@example.com"
  }'
```

### Create a Project
```bash
curl -X POST "http://localhost:8000/api/projects/" \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "AI Project Management Tool",
    "user_id": "507f1f77bcf86cd799439011"
  }'
```

### List User's Projects
```bash
curl "http://localhost:8000/api/projects/?user_id=507f1f77bcf86cd799439011"
```

## Task Reminders - Cron Setup

The task reminder system runs via cron to send daily reminders to team members.

### Manual Testing

```bash
cd api
source venv/bin/activate
python -m app.cadence.cron
```

### Production Cron (Linux/Mac)

```bash
# Edit crontab
crontab -e

# Add this line (runs every 15 minutes)
*/15 * * * * cd /path/to/api && /path/to/venv/bin/python -m app.cadence.cron >> /var/log/promarshal-cron.log 2>&1
```

### How It Works

1. **Free Tier**: Simple text reminder with all tasks
2. **Paid Tier**: Interactive buttons → Status selection → Modal → Comment → Update Jira

Cadence lives under `app/cadence/` (interactive paid-tier and free-tier reminder flows).

## Architecture

### Redis Queue Runtime

Background job processing for:
- Slack interactive components (button clicks, modals)
- Jira webhook processing
- Cortex run orchestration
- Action-item extraction

**Queues:**
- `chat_interactions` - Handles chat interaction callbacks (for example Slack/Teams modals and actions)
- `workitem_events` - Processes work-item provider event callbacks (for example Jira/Linear webhooks)
- `chat_commands` - Handles async chat command processing
- `cortex_runs` - Handles Cortex async run processing and continuation dispatch
- `action_item_extraction` - Processes action-item auto-detection from cadence summaries and Cortex task comments

**Runtime:**
- Dramatiq only
- see `WORKER_GUIDE.md` for startup and operational checks

**Benefits:**
- Fast webhook responses (< 100ms)
- Reliable retry mechanism
- Easy horizontal scaling

### Security

**OAuth Token Encryption:**
- All OAuth tokens (Slack, Jira) encrypted using Fernet (symmetric encryption)
- Keys stored in environment variable `ENCRYPTION_KEY`
- Tokens encrypted at rest in MongoDB

**OAuth State Protection:**
- CSRF protection via state tokens
- State tokens stored in encrypted cookies
- Expire after 10 minutes

## Environment Variables

```env
# MongoDB
MONGODB_URI=mongodb://localhost:27017/promarshal

# Redis
REDIS_URL=redis://localhost:6379

# Queue runtime
# Dramatiq is the only supported runtime.

# API Configuration
API_HOST=127.0.0.1
API_PORT=8000
API_RELOAD=True
BACKEND_URL=http://localhost:8000
FRONTEND_URL=http://localhost:3000

# CORS
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

# Security
ENCRYPTION_KEY=your-fernet-key-here

# Slack OAuth (optional)
SLACK_CLIENT_ID=your-slack-client-id
SLACK_CLIENT_SECRET=your-slack-client-secret

# Jira OAuth (optional)
JIRA_CLIENT_ID=your-jira-client-id
JIRA_CLIENT_SECRET=your-jira-client-secret

# Groq LLM (optional)
GROQ_API_KEY=your-groq-api-key

# Environment
ENVIRONMENT=development
```

## Tech Stack

- **Framework:** FastAPI 0.115.0
- **Language:** Python 3.10+
- **Database Driver:** Motor 3.6.0 (async MongoDB)
- **Queue:** Redis + Dramatiq
- **Validation:** Pydantic 2.9.2
- **Security:** Fernet encryption (cryptography)
- **HTTP Client:** HTTPX (async)
- **Server:** Uvicorn with auto-reload

## Project Structure

```
api/
├── app/
│   ├── core/                    # Core configuration
│   │   ├── config.py            # Settings
│   │   ├── database.py          # MongoDB connection
│   │   ├── redis_queue.py       # Redis & RQ setup
│   │   └── security.py          # Encryption, OAuth state
│   │
│   ├── integrations/            # External integrations
│   │   ├── router.py            # OAuth flows (Slack, Jira)
│   │   ├── slack/               # Slack API client
│   │   └── jira/                # Jira API client
│   │
│   ├── routes/                  # API routes
│   │   └── jira_webhooks.py     # Jira webhook handler
│   │
│   ├── cadence/                 # Cadence reminder/check-in system
│   │   ├── cron.py              # Cron entry point
│   │   ├── router.py            # Legacy interactions shim endpoint
│   │   ├── interaction_handler.py
│   │   └── ...
│   │
│   ├── users/                   # User management
│   ├── projects/                # Project management
│   └── main.py                  # FastAPI app
│
├── run.py                       # Start script
└── requirements.txt             # Dependencies
```

## Common Issues

### Redis connection failed
- Ensure Redis is running: `docker ps` or `redis-cli ping`
- Check `REDIS_URL` in `.env`

### Worker not processing jobs
- Confirm Dramatiq worker process is running.
- Check worker logs for enqueue reject reasons.

### Jira OAuth not working
- Verify redirect URI matches Jira app settings
- Check `JIRA_CLIENT_ID` and `JIRA_CLIENT_SECRET`

### Modal not opening in Slack
- Ensure worker runtime is running (`start_dramatiq_worker.py`)
- Check worker logs for errors
- Verify Slack app has interactive components enabled

## Learn More

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Motor (async MongoDB)](https://motor.readthedocs.io/)
- [Dramatiq](https://dramatiq.io/)
- [Slack API](https://api.slack.com/)
- [Jira API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/)
