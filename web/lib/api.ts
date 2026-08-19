const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000"

export interface ProjectMember {
  user_id?: string | null
  email?: string | null
  name?: string | null
  role: "owner" | "admin" | "member"
  status: "active" | "pending" | "declined" | "removed"
  invite_token?: string | null
  invited_by?: string | null
  invited_at?: string | null
  expires_at?: string | null
  joined_at?: string | null
}

export interface Project {
  _id: string
  project_id: string
  project_name: string
  user_id: string
  type?: string
  team_size?: string
  members?: ProjectMember[]
  invited_emails?: string[]
  tools_selected?: string[]  // Legacy - only for old projects
  tools_summary?: Record<string, any>  // Legacy - only for old projects
  integrations?: Record<string, any>  // New integration structure
  workflow_capabilities?: {
    has_sprints?: boolean
    has_epics?: boolean
  }
  created_at: string
  updated_at: string
}

export interface CreateProjectPayload {
  projectName: string
  userId: string
  teamSize?: string
  projectType?: string
  tier?: string
  timezone?: string
  invitedEmails?: string[]
  // Note: No longer sending toolsSelected for new projects
  // Integrations are added separately via the Integrate Tools tab
}

export interface CreateProjectResult {
  ok: boolean
  status: number
  project?: Project
  error?: string
}

export interface CadenceScheduleSettings {
  enabled: boolean
  time: string
}

export async function fetchProjects(backendToken?: string): Promise<Project[]> {
  try {
    const response = await fetch(
      `${PYTHON_API_URL}/api/projects`,
      {
        headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
        cache: "no-store", // Always fetch fresh data
        next: { revalidate: 0 }, // Never cache
      }
    )

    if (!response.ok) {
      console.error("Failed to fetch projects:", response.statusText)
      return []
    }

    const projects = await response.json()
    return projects
  } catch (error) {
    console.error("Error fetching projects:", error)
    return []
  }
}

export async function createProject(
  payload: CreateProjectPayload,
  backendToken?: string,
): Promise<CreateProjectResult> {
  try {
    const authHeader = String(backendToken || "").trim()
    const response = await fetch(`${PYTHON_API_URL}/api/projects`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(authHeader ? { Authorization: `Bearer ${authHeader}` } : {}),
      },
      body: JSON.stringify({
        project_name: payload.projectName,
        user_id: payload.userId,
        type: payload.projectType,
        team_size: payload.teamSize,
        tier: payload.tier,
        timezone: payload.timezone,
        invited_emails: payload.invitedEmails ?? [],
        // No longer sending tools_selected - integrations added separately
      }),
    })

    if (!response.ok) {
      let backendError = ""
      try {
        const errorPayload = await response.json()
        backendError = String(
          errorPayload?.detail ||
          errorPayload?.error ||
          response.statusText ||
          "Failed to create project"
        ).trim()
      } catch {
        backendError = String(response.statusText || "Failed to create project")
      }
      console.error("Failed to create project:", response.status, backendError)
      return { ok: false, status: response.status, error: backendError }
    }

    const project = await response.json()
    return { ok: true, status: response.status, project }
  } catch (error) {
    console.error("Error creating project:", error)
    return { ok: false, status: 500, error: "Failed to reach backend service" }
  }
}

export async function fetchCadenceSchedule(projectId: string): Promise<CadenceScheduleSettings | null> {
  try {
    const response = await fetch(`${PYTHON_API_URL}/api/projects/${projectId}/cadence-schedule`, {
      cache: "no-store",
      next: { revalidate: 0 },
    })
    if (!response.ok) {
      return null
    }
    return await response.json()
  } catch (error) {
    console.error("Error fetching cadence schedule:", error)
    return null
  }
}

export async function updateCadenceSchedule(
  projectId: string,
  payload: CadenceScheduleSettings
): Promise<CadenceScheduleSettings | null> {
  try {
    const response = await fetch(`${PYTHON_API_URL}/api/projects/${projectId}/cadence-schedule`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      return null
    }
    return await response.json()
  } catch (error) {
    console.error("Error updating cadence schedule:", error)
    return null
  }
}
