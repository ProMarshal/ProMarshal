"use client"

import { useEffect, useState } from "react"
import { useSession } from "next-auth/react"

interface Task {
  task_key: string
  title: string
  assignee_name: string
  url: string
}

interface TasksDueSoonWidgetProps {
  projectId: string
}

export function TasksDueSoonWidget({ projectId }: TasksDueSoonWidgetProps) {
  const { data: session } = useSession()
  const [tasksToday, setTasksToday] = useState<Task[]>([])
  const [tasksTomorrow, setTasksTomorrow] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const backendToken = String(session?.user?.backendToken || "").trim()

  useEffect(() => {
    const fetchTasks = async () => {
      setLoading(true)
      try {
        const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
        const authHeaders: HeadersInit = backendToken
          ? { Authorization: `Bearer ${backendToken}` }
          : {}

        const [todayRes, tomorrowRes] = await Promise.all([
          fetch(`${backendUrl}/api/projects/${projectId}/tasks-due-today`, { headers: authHeaders }),
          fetch(`${backendUrl}/api/projects/${projectId}/tasks-due-tomorrow`, { headers: authHeaders })
        ])

        if (todayRes.ok) {
          const data = await todayRes.json()
          setTasksToday(data)
        }
        if (tomorrowRes.ok) {
          const data = await tomorrowRes.json()
          setTasksTomorrow(data)
        }
      } catch (error) {
        console.error("Error fetching tasks:", error)
      } finally {
        setLoading(false)
      }
    }

    fetchTasks()
  }, [projectId, backendToken])

  if (loading) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <div className="flex items-center justify-center">
          <img src="/logos/loading.gif" alt="Loading..." className="w-8 h-8" />
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100 flex-shrink-0" style={{ background: 'linear-gradient(to right, rgba(120, 165, 48, 0.08), white)' }}>
        <h2 className="text-sm font-semibold text-gray-900">Tasks Due Soon</h2>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden p-4 space-y-4">
        {/* Due Today Section */}
        <div>
          <h3 className="text-xs font-semibold text-gray-700 mb-2">
            DUE TODAY ({tasksToday.length})
          </h3>
          {tasksToday.length === 0 ? (
            <p className="text-sm text-gray-400 italic">No tasks due today</p>
          ) : (
            <div
              className="space-y-0 overflow-y-auto hover:pr-2 transition-all"
              style={{
                maxHeight: '200px',
                scrollbarWidth: 'thin',
                scrollbarColor: 'transparent transparent'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.scrollbarColor = '#78a530 transparent'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.scrollbarColor = 'transparent transparent'
              }}
            >
              {tasksToday.map((task, index) => (
                <div key={task.task_key}>
                  <a
                    href={task.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-between py-2 px-3 hover:bg-gray-50 rounded-lg transition group"
                  >
                    <span className="text-sm text-gray-900 truncate flex-1 group-hover:text-[#78a530]">
                      {task.title}
                    </span>
                    <span className="text-sm text-gray-500 ml-2 flex-shrink-0">
                      • {task.assignee_name}
                    </span>
                  </a>
                  {index < tasksToday.length - 1 && (
                    <div className="border-b border-gray-100 mx-3" />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Due Tomorrow Section */}
        <div>
          <h3 className="text-xs font-semibold text-gray-700 mb-2">
            DUE TOMORROW ({tasksTomorrow.length})
          </h3>
          {tasksTomorrow.length === 0 ? (
            <p className="text-sm text-gray-400 italic">No tasks due tomorrow</p>
          ) : (
            <div
              className="space-y-0 overflow-y-auto hover:pr-2 transition-all"
              style={{
                maxHeight: '200px',
                scrollbarWidth: 'thin',
                scrollbarColor: 'transparent transparent'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.scrollbarColor = '#78a530 transparent'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.scrollbarColor = 'transparent transparent'
              }}
            >
              {tasksTomorrow.map((task, index) => (
                <div key={task.task_key}>
                  <a
                    href={task.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-between py-2 px-3 hover:bg-gray-50 rounded-lg transition group"
                  >
                    <span className="text-sm text-gray-900 truncate flex-1 group-hover:text-[#78a530]">
                      {task.title}
                    </span>
                    <span className="text-sm text-gray-500 ml-2 flex-shrink-0">
                      • {task.assignee_name}
                    </span>
                  </a>
                  {index < tasksTomorrow.length - 1 && (
                    <div className="border-b border-gray-100 mx-3" />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
