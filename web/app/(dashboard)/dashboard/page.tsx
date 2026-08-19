import { redirect } from "next/navigation"
import { auth } from "@/lib/auth"
import { fetchProjects } from "@/lib/api"
import { DashboardContent } from "@/components/dashboard/dashboard-content"

interface DashboardPageProps {
  searchParams: Promise<{ create?: string }>
}

export default async function DashboardPage({ searchParams }: DashboardPageProps) {
  const session = await auth()

  if (!session?.user) {
    redirect("/login")
  }

  const params = await searchParams
  const forceCreate = params.create === "true"

  const projects = await fetchProjects(String(session.user.backendToken || "").trim())

  // New users without projects should go through onboarding
  if (projects.length === 0 && !forceCreate) {
    redirect("/project-setup")
  }

  // If user wants to create a new project, show onboarding flow even if they have projects
  // Otherwise, if user has projects, go directly to projects page
  if (!forceCreate && projects.length > 0) {
    redirect("/projects")
  }

  // Show onboarding flow (forced by "create new")
  return (
    <DashboardContent
      initialProjects={projects}
      userId={session.user.userId || session.user.id || ""}
      userName={session.user.name || ""}
    />
  )
}
