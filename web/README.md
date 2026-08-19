# ProMarshal Frontend

Next.js 16 frontend for ProMarshal - AI-powered project management assistant.

## Tech Stack

- **Framework:** Next.js 16 (App Router, React 19)
- **Language:** TypeScript
- **Styling:** Tailwind CSS v4 (@theme syntax)
- **Authentication:** NextAuth.js v5 (beta)
- **State Management:** React hooks + Server Components
- **API Communication:** Fetch API to Python backend
- **UI Components:** Custom components + shadcn/ui patterns

## Setup

### 1. Install Dependencies

```bash
cd web
npm install
```

### 2. Configure Environment

Copy `.env.example` to `.env.local`:

```bash
cp .env.example .env.local
```

Update `.env.local`:

```env
# NextAuth v5
AUTH_SECRET=your-secret-from-openssl-rand-base64-32
NEXTAUTH_URL=http://localhost:3000

# Google OAuth
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# Python Backend
PYTHON_API_URL=http://localhost:8000
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Generate `AUTH_SECRET`:
```bash
openssl rand -base64 32
```

### 3. Start Development Server

```bash
npm run dev
```

App runs on: `http://localhost:3000`

### 4. Build for Production

```bash
npm run build
npm start
```

## Project Structure

```
web/
├── app/
│   ├── (auth)/                        # Auth route group
│   │   ├── login/page.tsx             # Login with Google
│   │   ├── signup/page.tsx            # Signup with Google
│   │   └── actions.ts                 # Auth server actions
│   │
│   ├── (dashboard)/                   # Dashboard route group
│   │   ├── layout.tsx                 # Sidebar layout
│   │   ├── dashboard/page.tsx         # Main dashboard
│   │   └── projects/page.tsx          # Projects page
│   │
│   ├── api/
│   │   ├── auth/[...nextauth]/        # NextAuth routes
│   │   └── projects/create/           # Create project API
│   │
│   ├── page.tsx                       # Landing page
│   ├── layout.tsx                     # Root layout
│   └── globals.css                    # Tailwind + theme
│
├── components/
│   ├── dashboard/
│   │   ├── create-project-modal.tsx   # Step 1: Project name
│   │   ├── integration-modal.tsx      # Step 2: Slack/Jira
│   │   ├── empty-state.tsx            # No projects view
│   │   ├── dashboard-content.tsx      # Main content logic
│   │   └── sidebar.tsx                # Navigation sidebar
│   │
│   ├── onboarding/
│   │   └── project-setup-form.tsx     # Project creation flow
│   │
│   └── ui/                            # Shared UI components
│       ├── button.tsx
│       ├── modal.tsx
│       └── input.tsx
│
├── lib/
│   ├── auth.ts                        # NextAuth config + callbacks
│   └── api.ts                         # Python API helpers
│
├── types/
│   └── next-auth.d.ts                 # TypeScript types
│
├── public/
│   ├── integrations/                  # Slack/Jira icons (128x128)
│   └── illustrations/                 # Empty state image (400px)
│
└── package.json
```

## Key Features

### Authentication (NextAuth v5)

- Google OAuth 2.0 integration
- Session management with JWT
- Protected routes with middleware
- User creation flow

**Flow:**
```
User → Google OAuth → NextAuth v5
  ↓
Extract user_id from email (e.g., "john" from "john@gmail.com")
  ↓
Check if user exists in MongoDB via Python API
  ↓
If not → POST /api/users/ → Save to DB
  ↓
Create session with user_id, name, email
  ↓
Redirect to /dashboard
```

### Dashboard

- Project listing and creation
- Integration management (Slack/Jira)
- Responsive sidebar navigation
- Empty states with illustrations

### Integrations

- OAuth flow for Slack workspace connection
- OAuth flow for Jira site connection
- Real-time integration status display
- Disconnect functionality

### Design System

**Color Palette:**
```css
Primary: Linear gradient (#7c3aed → #6366f1)  /* Purple to Indigo */
Background: #ffffff                            /* White */
Text (Headings): #0f172a                      /* Gray-900 */
Text (Body): #64748b                          /* Gray-600 */
Borders: #e2e8f0                              /* Gray-200 */
Hover: Purple-400 borders, shadow-purple-500/30
```

**Typography:**
- Font: Geist Sans (system fallback)
- Headings: font-extrabold (800), text-gray-900
- Body: font-medium (500), text-gray-600
- Buttons: font-semibold (600)

**Components:**
- Buttons: `gradient-primary` class, `rounded-full`, shadow-lg
- Cards: `rounded-2xl`, hover effects with scale
- Modals: White bg, backdrop blur

## API Communication

### Fetching Data (Server Components)

```typescript
// app/dashboard/page.tsx
import { getProjects } from '@/lib/api';

export default async function DashboardPage() {
  const projects = await getProjects(userId);
  return <ProjectList projects={projects} />;
}
```

### Mutations (Client Components)

```typescript
// components/create-project-modal.tsx
'use client';

const handleCreate = async () => {
  const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/projects/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_name, user_id })
  });
  const project = await response.json();
};
```

## Development

### Adding a New Page

1. Create file in `app/(dashboard)/newpage/page.tsx`
2. Add route to sidebar in `components/dashboard/sidebar.tsx`
3. Protected by default (requires auth)

### Adding a New Component

1. Create in `components/feature-name/component.tsx`
2. Use `'use client'` if it needs interactivity
3. Import and use in pages

### Styling Guidelines

- Use Tailwind CSS utilities
- Follow existing color scheme
- Use `@theme` syntax for custom tokens
- Maintain responsive design (mobile-first)

## Environment Variables

### Required

- `AUTH_SECRET` - NextAuth secret key
- `GOOGLE_CLIENT_ID` - Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` - Google OAuth secret
- `PYTHON_API_URL` - Backend API URL (server-side)
- `NEXT_PUBLIC_API_URL` - Backend API URL (client-side)

### Optional

- `NEXTAUTH_URL` - Base URL (defaults to http://localhost:3000)

## Common Issues

### "Invalid hook call" error
- Ensure `'use client'` is at the top of client components
- Check you're not importing client components in server components

### Auth not working
- Verify `AUTH_SECRET` is set
- Check Google OAuth redirect URI matches
- Ensure cookies are enabled

### API calls failing
- Verify backend is running on port 8000
- Check CORS settings in backend
- Confirm environment variables are set

## Scripts

```bash
npm run dev          # Start development server
npm run build        # Build for production
npm run start        # Start production server
npm run lint         # Run ESLint
npm run type-check   # Run TypeScript check
```

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
- [NextAuth.js v5](https://authjs.dev/)
- [Tailwind CSS v4](https://tailwindcss.com/docs)
- [TypeScript](https://www.typescriptlang.org/docs/)
