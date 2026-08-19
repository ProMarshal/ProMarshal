"use client"

import { useState, useEffect } from "react"
import { Loading } from "@/components/ui/loading"

interface JiraProject {
  key: string
  name: string
  id: string
}

interface JiraSite {
  id: string
  name: string
  url: string
}

interface JiraProjectSelectionModalProps {
  isOpen: boolean
  onClose: () => void
  projectId: string
  userId: string
  backendToken?: string
  onSuccess?: () => void
  isDarkMode?: boolean
}

export function JiraProjectSelectionModal({
  isOpen,
  onClose,
  projectId,
  userId,
  backendToken,
  onSuccess,
  isDarkMode = false,
}: JiraProjectSelectionModalProps) {
  const dm = isDarkMode
  const [availableProjects, setAvailableProjects] = useState<JiraProject[]>([])
  const [availableSites, setAvailableSites] = useState<JiraSite[]>([])
  const [selectionMode, setSelectionMode] = useState<"site" | "project">("site")
  const [selectedCloudId, setSelectedCloudId] = useState<string>("")
  const [selectedProjectKey, setSelectedProjectKey] = useState<string>("")
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<string>("")

  useEffect(() => {
    if (isOpen && projectId) {
      console.log('[Jira Modal] Opening with projectId:', projectId)
      fetchAvailableProjects()
    }
  }, [isOpen, projectId])

  const fetchAvailableProjects = async () => {
    try {
      setIsLoading(true)
      setError("")

      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      console.log('[Jira Modal] Fetching project:', projectId)
      const response = await fetch(
        `${backendUrl}/api/projects/${projectId}`,
        {
          cache: "no-store",
          headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
        }
      )

      if (!response.ok) {
        throw new Error("Failed to fetch project data")
      }

      const project = await response.json()
      console.log('[Jira Modal] Project data:', project)
      const jiraIntegration = project?.integrations?.jira
      console.log('[Jira Modal] Jira integration:', jiraIntegration)

      if (!jiraIntegration) {
        throw new Error("Jira integration is missing")
      }

      const status = String(jiraIntegration.status || "").trim().toLowerCase()
      if (status === "pending_site_selection") {
        const sites = jiraIntegration.available_sites || []
        console.log('[Jira Modal] Available sites:', sites)
        setSelectionMode("site")
        setAvailableSites(sites)
        setAvailableProjects([])
        if (sites.length > 0) {
          setSelectedCloudId(sites[0].id)
        }
        return
      }

      if (status === "pending_project_selection") {
        const projects = jiraIntegration.available_projects || []
        console.log('[Jira Modal] Available projects:', projects)
        setSelectionMode("project")
        setAvailableProjects(projects)
        if (projects.length > 0) {
          setSelectedProjectKey(projects[0].key)
        }
        return
      }

      throw new Error("Jira integration is not in pending selection state")
    } catch (err: any) {
      setError(err.message || "Failed to load Jira projects")
      console.error("[Jira Modal] Error fetching Jira projects:", err)
    } finally {
      setIsLoading(false)
    }
  }

  const handleSubmit = async () => {
    if (selectionMode === "site" && !selectedCloudId) {
      setError("Please select a Jira site")
      return
    }

    if (selectionMode === "project" && !selectedProjectKey) {
      setError("Please select a Jira project")
      return
    }

    try {
      setIsSaving(true)
      setError("")

      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const url = selectionMode === "site"
        ? `${backendUrl}/api/integrations/jira/select-site/${projectId}?selected_cloud_id=${selectedCloudId}`
        : `${backendUrl}/api/integrations/jira/select-project/${projectId}?selected_project_key=${selectedProjectKey}`
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(backendToken ? { Authorization: `Bearer ${backendToken}` } : {}),
        }
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || "Failed to select Jira project")
      }

      if (selectionMode === "site") {
        await fetchAvailableProjects()
        return
      }

      if (onSuccess) {
        onSuccess()
      } else {
        window.location.reload()
      }
    } catch (err: any) {
      setError(err.message || "Failed to save project selection")
      console.error("Error saving project selection:", err)
    } finally {
      setIsSaving(false)
    }
  }

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 flex items-center justify-center"
      style={{
        backgroundColor: 'rgba(0, 0, 0, 0.4)',
        zIndex: 9999
      }}
    >
      <div
        className={`rounded-xl p-6 max-w-md w-full mx-4 shadow-2xl ${dm ? "text-gray-100" : "bg-white text-gray-900"}`}
        style={dm ? {
          position: 'relative',
          zIndex: 10000,
          background: "linear-gradient(135deg, rgba(30,38,56,0.95) 0%, rgba(17,21,32,0.98) 100%)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          border: "1px solid rgba(255,255,255,0.18)",
          boxShadow: "0 8px 40px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.12)",
        } : { position: 'relative', zIndex: 10000 }}
      >
        <h2 className={`text-xl font-semibold mb-4 ${dm ? "text-gray-100" : "text-gray-900"}`}>
          {selectionMode === "site" ? "Select Jira Site" : "Select Jira Project"}
        </h2>

        {isLoading ? (
          <div className="text-center py-8">
            <Loading size="md" message="Loading Jira projects..." />
          </div>
        ) : error ? (
          <div className="mb-4 p-3 bg-red-900/30 border border-red-700/40 rounded text-red-400 text-sm">
            {error}
          </div>
        ) : (
          <>
            {selectionMode === "site" ? (
              <>
                <p className={`mb-4 text-sm ${dm ? "text-gray-400" : "text-gray-600"}`}>
                  Choose which Jira site ProMarshal should connect for this project.
                </p>
                <div className="mb-6">
                  <label className={`block text-sm font-medium mb-2 ${dm ? "text-gray-300" : "text-gray-700"}`}>
                    Jira Site
                  </label>
                  <select
                    value={selectedCloudId}
                    onChange={(e) => setSelectedCloudId(e.target.value)}
                    className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent ${dm ? "bg-[#111520] border-white/[0.12] text-gray-100" : "border-gray-300 text-gray-900"}`}
                    disabled={isSaving}
                  >
                    {availableSites.map((site) => (
                      <option key={site.id} value={site.id}>
                        {site.name || site.url}
                      </option>
                    ))}
                  </select>
                </div>
              </>
            ) : (
              <>
                <p className={`mb-4 text-sm ${dm ? "text-gray-400" : "text-gray-600"}`}>
                  Choose which Jira project ProMarshal should use for task creation and management.
                </p>
                <div className="mb-6">
                  <label className={`block text-sm font-medium mb-2 ${dm ? "text-gray-300" : "text-gray-700"}`}>
                    Jira Project
                  </label>
                  <select
                    value={selectedProjectKey}
                    onChange={(e) => setSelectedProjectKey(e.target.value)}
                    className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent ${dm ? "bg-[#111520] border-white/[0.12] text-gray-100" : "border-gray-300 text-gray-900"}`}
                    disabled={isSaving}
                  >
                    {availableProjects.map((project) => (
                      <option key={project.key} value={project.key}>
                        {project.name} ({project.key})
                      </option>
                    ))}
                  </select>
                </div>
              </>
            )}

            <div className="flex gap-3 justify-end">
              <button
                onClick={onClose}
                disabled={isSaving}
                className={`px-4 py-2 rounded-md transition-colors disabled:opacity-50 ${dm ? "text-gray-300 hover:bg-white/[0.08]" : "text-gray-700 hover:bg-gray-100"}`}
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={isSaving || (selectionMode === "site" ? !selectedCloudId : !selectedProjectKey)}
                className="px-4 py-2 bg-[#78a530] text-white rounded-md hover:bg-[#6a9129] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSaving ? "Saving..." : "Continue"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
