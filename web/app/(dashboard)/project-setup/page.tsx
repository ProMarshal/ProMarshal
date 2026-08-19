import { auth } from "@/lib/auth"
import { redirect } from "next/navigation"
import { fetchProjects } from "@/lib/api"
import { DashboardContent } from "@/components/dashboard/dashboard-content"

export default async function ProjectSetupPage() {
  const session = await auth()

  if (!session?.user) {
    redirect("/login")
  }

  const projects = await fetchProjects(String(session.user.backendToken || "").trim())

  return (
    <DashboardContent
      initialProjects={projects}
      userId={session.user.userId || session.user.id || ""}
      userName={session.user.name || ""}
      forceSetup={true}
    />
  )
}
