"use client"

import { useState, useEffect } from "react"
import { useSession } from "next-auth/react"
import { Loading } from "@/components/ui/loading"

interface TaskDetail {
  external_id: string
  title: string
  url: string
  priority: string
}

interface TeamMember {
  name: string
  email: string
  completed: number
  in_progress: number
  todo: number
  total: number
  capacity_percentage: number
  is_overloaded: boolean
  completed_tasks: TaskDetail[]
  in_progress_tasks: TaskDetail[]
  todo_tasks: TaskDetail[]
}

interface TeamWorkloadWidgetProps {
  projectId: string
}

const CARDS_PER_PAGE = 3

export function TeamWorkloadWidget({ projectId }: TeamWorkloadWidgetProps) {
  const { data: session } = useSession()
  const [workload, setWorkload] = useState<TeamMember[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activePopover, setActivePopover] = useState<{ memberEmail: string; status: string } | null>(null)
  const [currentIndex, setCurrentIndex] = useState(0)
  const backendToken = String(session?.user?.backendToken || "").trim()

  useEffect(() => {
    const fetchWorkload = async () => {
      try {
        setIsLoading(true)
        setError(null)
        const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
        const response = await fetch(`${backendUrl}/api/projects/${projectId}/team-workload`, {
          headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
        })
        if (!response.ok) throw new Error("Failed to fetch team workload")
        const data = await response.json()
        setWorkload(data)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load workload data")
      } finally {
        setIsLoading(false)
      }
    }
    if (projectId) fetchWorkload()
  }, [projectId, backendToken])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (activePopover && !(event.target as Element).closest('.task-status-button, .task-popover')) {
        setActivePopover(null)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [activePopover])

  const totalPages = Math.ceil(workload.length / CARDS_PER_PAGE)
  const visibleMembers = workload.slice(currentIndex, currentIndex + CARDS_PER_PAGE)
  const canGoPrev = currentIndex > 0
  const canGoNext = currentIndex + CARDS_PER_PAGE < workload.length

  const handlePrev = () => {
    if (canGoPrev) setCurrentIndex(currentIndex - CARDS_PER_PAGE)
  }

  const handleNext = () => {
    if (canGoNext) setCurrentIndex(currentIndex + CARDS_PER_PAGE)
  }

  if (isLoading) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 flex items-center justify-center" style={{ minHeight: '200px' }}>
        <Loading size="md" message="Loading team..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-white rounded-xl border border-red-200 shadow-sm p-6">
        <p className="text-red-600 text-sm">Error: {error}</p>
      </div>
    )
  }

  if (workload.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
        <h2 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
          <svg className="w-5 h-5 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
          </svg>
          Team at a Glance
        </h2>
        <p className="text-sm text-gray-500">No team members with tasks yet</p>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm flex flex-col overflow-visible">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100 flex-shrink-0 flex items-center justify-between" style={{ background: 'linear-gradient(to right, rgba(120, 165, 48, 0.08), white)' }}>
        <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <svg className="w-5 h-5 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
          </svg>
          Team at a Glance
        </h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-[#78a530] font-semibold">
            {workload.length} {workload.length === 1 ? "member" : "members"}
          </span>
          {/* Arrows */}
          <div className="flex items-center gap-1">
            <button
              onClick={handlePrev}
              disabled={!canGoPrev}
              className={`w-7 h-7 rounded-full flex items-center justify-center border transition ${canGoPrev ? 'border-gray-300 hover:border-[#78a530] hover:text-[#78a530] text-gray-600' : 'border-gray-100 text-gray-300 cursor-not-allowed'}`}
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
              </svg>
            </button>
            <span className="text-xs text-gray-400 font-medium w-10 text-center">
              {Math.floor(currentIndex / CARDS_PER_PAGE) + 1} / {totalPages}
            </span>
            <button
              onClick={handleNext}
              disabled={!canGoNext}
              className={`w-7 h-7 rounded-full flex items-center justify-center border transition ${canGoNext ? 'border-gray-300 hover:border-[#78a530] hover:text-[#78a530] text-gray-600' : 'border-gray-100 text-gray-300 cursor-not-allowed'}`}
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Carousel Cards */}
      <div className="p-4">
        <div className="grid grid-cols-3 gap-3">
          {visibleMembers.map((member) => {
            const activeTasksCount = member.in_progress + member.todo

            return (
              <div
                key={member.email}
                className={`relative bg-white border rounded-xl p-4 transition hover:shadow-md ${member.is_overloaded ? 'border-orange-200' : 'border-gray-200 hover:border-[#78a530]'}`}
              >
                {/* Avatar + Name */}
                <div className="flex flex-col items-center text-center mb-4">
                  <div className="w-12 h-12 rounded-full bg-gradient-to-br from-[#78a530] to-[#5a7d24] flex items-center justify-center text-white font-bold text-lg mb-2 shadow-sm">
                    {member.name.charAt(0).toUpperCase()}
                  </div>
                  <p className="text-sm font-semibold text-gray-900 leading-tight">{member.name}</p>
                  <span className={`mt-1.5 text-xs font-semibold px-2 py-0.5 rounded-full ${
                    activeTasksCount === 0
                      ? 'bg-gray-100 text-gray-500'
                      : member.capacity_percentage >= 70
                      ? 'bg-orange-100 text-orange-700'
                      : member.capacity_percentage >= 40
                      ? 'bg-yellow-100 text-yellow-700'
                      : 'bg-green-100 text-green-700'
                  }`}>
                    {activeTasksCount === 0 ? 'Available' : member.capacity_percentage >= 70 ? 'High Load' : member.capacity_percentage >= 40 ? 'Active' : 'Light Load'}
                  </span>
                </div>

                {/* Status Counts */}
                <div className="flex justify-around text-center mb-3 relative">
                  <button
                    onClick={() => setActivePopover(
                      activePopover?.memberEmail === member.email && activePopover?.status === 'todo'
                        ? null : { memberEmail: member.email, status: 'todo' }
                    )}
                    disabled={member.todo === 0}
                    className={`task-status-button flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg transition ${member.todo > 0 ? 'hover:bg-gray-50 cursor-pointer' : 'cursor-default'}`}
                  >
                    <span className="text-lg font-extrabold text-gray-700">{member.todo}</span>
                    <span className="text-xs text-gray-400 font-medium">To Do</span>
                  </button>

                  <div className="w-px bg-gray-100 self-stretch"></div>

                  <button
                    onClick={() => setActivePopover(
                      activePopover?.memberEmail === member.email && activePopover?.status === 'in_progress'
                        ? null : { memberEmail: member.email, status: 'in_progress' }
                    )}
                    disabled={member.in_progress === 0}
                    className={`task-status-button flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg transition ${member.in_progress > 0 ? 'hover:bg-orange-50 cursor-pointer' : 'cursor-default'}`}
                  >
                    <span className="text-lg font-extrabold text-orange-500">{member.in_progress}</span>
                    <span className="text-xs text-gray-400 font-medium">In Progress</span>
                  </button>

                  <div className="w-px bg-gray-100 self-stretch"></div>

                  <button
                    onClick={() => setActivePopover(
                      activePopover?.memberEmail === member.email && activePopover?.status === 'completed'
                        ? null : { memberEmail: member.email, status: 'completed' }
                    )}
                    disabled={member.completed === 0}
                    className={`task-status-button flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg transition ${member.completed > 0 ? 'hover:bg-green-50 cursor-pointer' : 'cursor-default'}`}
                  >
                    <span className="text-lg font-extrabold text-[#78a530]">{member.completed}</span>
                    <span className="text-xs text-gray-400 font-medium">Done</span>
                  </button>
                </div>

                {/* Progress Bar */}
                <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
                  <div
                    className="h-full bg-[#78a530] transition-all duration-500"
                    style={{ width: `${member.total > 0 ? (member.completed / member.total) * 100 : 0}%` }}
                  />
                </div>
                <p className="text-xs text-gray-400 text-center mt-1">
                  {member.total > 0 ? Math.round((member.completed / member.total) * 100) : 0}% complete
                </p>

                {/* Popover */}
                {activePopover?.memberEmail === member.email && (
                  <div className="task-popover absolute left-0 top-full mt-2 z-50 bg-white border-2 border-gray-200 rounded-xl shadow-2xl p-4 min-w-[280px] max-w-[340px]">
                    <button
                      onClick={() => setActivePopover(null)}
                      className="absolute top-3 right-3 text-gray-400 hover:text-gray-600"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                    <h3 className="text-sm font-bold text-gray-900 mb-3">
                      {activePopover.status === 'completed' && `Completed Tasks (${member.completed})`}
                      {activePopover.status === 'in_progress' && `In Progress Tasks (${member.in_progress})`}
                      {activePopover.status === 'todo' && `To Do Tasks (${member.todo})`}
                    </h3>
                    <div className="max-h-[260px] overflow-y-auto space-y-2">
                      {(activePopover.status === 'completed' ? member.completed_tasks
                        : activePopover.status === 'in_progress' ? member.in_progress_tasks
                        : member.todo_tasks
                      ).map((task) => (
                        <a
                          key={task.external_id}
                          href={task.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-start gap-2 p-2 rounded-lg hover:bg-gray-50 border border-gray-100 transition"
                        >
                          <span className={`text-xs font-mono px-1.5 py-0.5 rounded font-bold flex-shrink-0 ${
                            activePopover.status === 'completed' ? 'bg-green-100 text-green-700'
                            : activePopover.status === 'in_progress' ? 'bg-orange-100 text-orange-700'
                            : 'bg-gray-100 text-gray-700'
                          }`}>
                            {task.external_id}
                          </span>
                          <span className="text-sm text-gray-800 line-clamp-2">{task.title}</span>
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
