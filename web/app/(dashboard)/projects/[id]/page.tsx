import { redirect } from "next/navigation"

interface ProjectPageProps {
  params: Promise<{ id: string }>
}

export default async function ProjectPage({ params }: ProjectPageProps) {
  // Redirect to the projects page
  redirect("/projects")
}
