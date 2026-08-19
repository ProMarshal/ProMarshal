# ProMarshal

AI-powered project management assistant that automates coordination work.

## 🚀 Quick Start

### Prerequisites
- Node.js 18+ (for Next.js frontend)
- Python 3.10+ (for FastAPI backend)
- MongoDB (local or Atlas)
- Redis (for queues and caching)
- Docker (recommended for Redis)
- Google OAuth credentials
- Slack App credentials (optional, for Slack integration)
- Jira OAuth credentials (optional, for Jira integration)
- Groq API key (optional, for AI-powered messages)

### 1. Clone Repository
```bash
git clone https://github.com/promarshal/promarshal.git
cd promarshal
```

### 2. Start Backend

```bash
cd api

# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate
# Activate (Mac/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your MongoDB URI

# Start server
python run.py
```

Backend runs on: `http://127.0.0.1:8000`

### 3. Start Redis

```bash
# Using Docker (recommended)
docker run -d -p 6379:6379 redis:latest

# Or install Redis locally
# Mac: brew install redis && brew services start redis
# Ubuntu: sudo apt install redis-server && sudo systemctl start redis
```

### 4. Start RQ Worker (Background Jobs)

```bash
cd api
source venv/bin/activate

# Set environment variable for macOS
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

# Start worker
rq worker slack_interactions jira_webhooks action_item_extraction --url redis://localhost:6379
```

Keep this terminal running for background job processing.

### 5. Start Frontend

```bash
cd web

# Install dependencies
npm install

# Configure environment
cp .env.example .env.local
# Edit .env.local with:
# - AUTH_SECRET (generate with: openssl rand -base64 32)
# - GOOGLE_CLIENT_ID
# - GOOGLE_CLIENT_SECRET

# Start dev server
npm run dev
```

Frontend runs on: `http://localhost:3000`

### 6. Setup Google OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create OAuth 2.0 credentials
3. Add redirect URI: `http://localhost:3000/api/auth/callback/google`
4. Copy Client ID and Secret to `.env.local`

## 📚 Documentation

- **[CLAUDE.md](./CLAUDE.md)** - Complete project context and implementation status
- **[PROJECT_STRUCTURE.md](./PROJECT_STRUCTURE.md)** - Detailed folder structure
- **[api/README.md](./api/README.md)** - Backend API documentation

## 🎨 Current Features

### Core Platform
✅ Landing page with professional design
✅ Google OAuth authentication
✅ Dashboard with project management
✅ Create project modal flow
✅ MongoDB data persistence
✅ FastAPI backend with CRUD operations

### Integrations (Fully Implemented)
✅ **Slack OAuth Integration** - Connect workspace, send messages, interactive buttons
✅ **Jira OAuth Integration** - Sync tasks, update status, add comments
✅ **Jira Webhooks** - Real-time task updates from Jira to ProMarshal DB

### Task Reminders System
✅ **Free Tier** - Simple daily task reminders via Slack DM
✅ **Paid Tier** - Interactive task updates with buttons and modals
✅ **Cron Scheduler** - Automated daily reminders at configured time
✅ **Session Management** - Track user progress through tasks
✅ **Jira Sync** - Update Jira tasks directly from Slack

### Infrastructure
✅ **Redis Queue (RQ)** - Background job processing for webhooks
✅ **Groq LLM Integration** - AI-powered conversational messages (optional)
✅ **Encryption** - Secure storage of OAuth tokens

## 🛠️ Tech Stack

**Frontend:** Next.js 16, TypeScript, Tailwind CSS v4, NextAuth v5
**Backend:** Python 3.10+, FastAPI, Motor (async MongoDB)
**Database:** MongoDB
**Queue:** Redis, RQ (Python-RQ)
**Auth:** Google OAuth 2.0, Slack OAuth, Jira OAuth
**AI:** Groq LLM (conversational messages)
**Integrations:** Slack API, Jira REST API
**Security:** Fernet encryption for tokens

## 📂 Project Structure

```
promarshal/
├── web/          # Next.js frontend
├── api/          # Python FastAPI backend
├── CLAUDE.md     # Project documentation
└── README.md     # This file
```

## 🔗 API Endpoints

**Users:**
- `POST /api/users/` - Create user
- `GET /api/users/email/{email}` - Get by email
- `GET /api/users/{user_id}` - Get by ID

**Projects:**
- `POST /api/projects/` - Create project
- `GET /api/projects?user_id={id}` - List user's projects
- `GET /api/projects/{project_id}` - Get by ID

**Integrations:**
- `GET /api/integrations/slack/connect` - Initiate Slack OAuth
- `GET /api/integrations/slack/callback` - Slack OAuth callback
- `DELETE /api/integrations/slack/disconnect` - Disconnect Slack
- `GET /api/integrations/jira/connect` - Initiate Jira OAuth
- `GET /api/integrations/jira/callback` - Jira OAuth callback
- `DELETE /api/integrations/jira/disconnect` - Disconnect Jira

**Task Reminders (Webhooks):**
- `POST /api/integrations/slack/interactions` - Handle Slack button clicks & modals

**Jira Webhooks:**
- `POST /api/jira/webhooks/task-updated` - Receive Jira task updates

**Docs:** `http://localhost:8000/docs`

## 🎯 Next Steps

- [ ] Meeting transcription and action item extraction (Google Meet integration)
- [ ] Email integration for communication tracking
- [ ] AI-powered project insights and recommendations
- [ ] Advanced team member management and permissions
- [ ] ClickUp integration
- [ ] Linear integration
- [ ] Notion integration
- [ ] Project knowledge base / memory system
- [ ] Analytics dashboard

## 📄 License

[Add your license here]

## 👥 Contributors

[Add contributors]

---

**Current Status:** MVP Phase 2 Complete ✅
**Live Features:** Slack Integration, Jira Integration, Task Reminders (Free & Paid Tier)
