import { redirect } from "next/navigation"
import { cookies } from "next/headers"
import { auth } from "@/lib/auth"
import { ProjectsPage } from "@/components/projects/projects-page"

export default async function ProjectsPageRoute({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>
}) {
  const session = await auth()

  if (!session?.user?.id) {
    redirect("/login")
  }

  const backendUrl =
    process.env.PYTHON_API_URL ||
    process.env.NEXT_PUBLIC_PYTHON_API_URL ||
    "http://localhost:8000"

  // Use session.user.userId (not .id) because projects are stored with user_id field
  const userId = session.user.userId || session.user.id
  const backendToken = String(session.user.backendToken || "").trim()

  // Fetch user's projects via canonical path (no redirect/cache-buster churn)
  const projectsResponse = await fetch(`${backendUrl}/api/projects`, {
    headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
    cache: "no-store",
    next: { revalidate: 0 },
  })

  if (projectsResponse.status === 401 || projectsResponse.status === 403) {
    redirect("/login")
  }

  if (!projectsResponse.ok) {
    throw new Error(
      `Failed to fetch projects from backend (${projectsResponse.status})`
    )
  }

  const payload = await projectsResponse.json()
  const projects = Array.isArray(payload) ? payload : []

  // If user has no projects, redirect to project setup
  if (projects.length === 0) {
    redirect("/project-setup")
  }

  // Await searchParams (Next.js 15+ requirement)
  const params = await searchParams

  // Get initial nav/tab from URL params to prevent flash
  const initialNav = typeof params.nav === 'string' ? params.nav : undefined
  const initialTab = typeof params.tab === 'string' ? params.tab : undefined

  // Get last selected project from cookie (persists across refreshes)
  const cookieStore = await cookies()
  const lastProjectId = cookieStore.get('lastSelectedProjectId')?.value

  // Find initial project: URL param > Cookie > First project
  const projectIdFromUrl = typeof params.projectId === 'string' ? params.projectId : undefined
  const selectedProjectId = projectIdFromUrl || lastProjectId

  const initialProject = selectedProjectId
    ? projects.find((p: any) => p._id === selectedProjectId) || projects[0]
    : projects[0]

  // Projects page - user must have at least 1 project to see this
  return (
    <ProjectsPage
      userName={session.user.name || ""}
      userId={userId}
      backendToken={backendToken}
      currentProject={initialProject || null}
      projects={projects}
      initialNav={initialNav}
      initialTab={initialTab}
      backendUrl={backendUrl}
    />
  )
}
