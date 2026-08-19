"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { Project } from "@/lib/api"

interface ProjectSwitcherProps {
  projects: Project[]
  currentProjectId: string
  onCreate: () => void
}

export function ProjectSwitcher({ projects, currentProjectId, onCreate }: ProjectSwitcherProps) {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)

  const currentProject = projects.find((project) => project._id === currentProjectId)

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }

    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const handleSelect = (projectId: string) => {
    setOpen(false)
    router.push(`/projects/${projectId}`)
  }

  return (
    <div className="relative" ref={menuRef}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="inline-flex items-center gap-3 rounded-2xl border border-gray-200 bg-white px-4 py-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow"
      >
        <div className="flex flex-col">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">Project</span>
          <span className="text-lg font-bold text-gray-900">
            {currentProject?.project_name || "Select a project"}
          </span>
        </div>
        <svg
          className={`h-5 w-5 text-gray-600 transition ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 mt-2 w-72 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl ring-1 ring-black/5">
          <div className="max-h-80 overflow-y-auto">
            {projects.map((project) => (
              <button
                key={project._id}
                type="button"
                onClick={() => handleSelect(project._id)}
                className={`flex w-full items-center justify-between px-4 py-3 text-left text-sm transition hover:bg-gray-50 ${
                  project._id === currentProjectId ? "bg-purple-50/70 text-purple-900" : "text-gray-800"
                }`}
              >
                <span className="font-medium">{project.project_name}</span>
                {project._id === currentProjectId && (
                  <span className="rounded-full bg-purple-100 px-2 py-0.5 text-xs font-semibold text-purple-800">
                    Current
                  </span>
                )}
              </button>
            ))}
          </div>
          <div className="border-t border-gray-200">
            <button
              type="button"
              onClick={() => {
                setOpen(false)
                onCreate()
              }}
              className="flex w-full items-center gap-2 px-4 py-3 text-sm font-semibold text-purple-700 transition hover:bg-purple-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Create project
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
