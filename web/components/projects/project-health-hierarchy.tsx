"use client"

import React, { useCallback, useEffect, useMemo, useState } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useSession } from "next-auth/react"
import { renderWorkItemIcon } from "@/components/projects/work-item-icons"

interface ProjectHealthHierarchyProps {
  projectId: string
  mongoId: string
  backendUrl: string
  refreshSignal?: number
  onRefreshStateChange?: (refreshing: boolean) => void
  initialHealthData?: any | null
  fallbackHealthStatus?: "on_track" | "at_risk" | "critical" | null
  onConsumeFallbackHealthStatus?: () => void
  recommendationByTaskKey?: Record<string, string>
  recommendationAlerts?: RecommendationAlert[]
  isDarkMode?: boolean
  recPanelOpen?: boolean
  onRecPanelClose?: () => void
  onRecommendationCountChange?: (counts: { workItem: number; bestPractice: number }) => void
}

interface RecommendationAlert {
  id: string
  taskKey?: string | null
  title: string
  reason: string
  recommendation: string
  severity: "critical" | "at_risk" | "info"
  category?: "best_practice" | "work_item"
}

interface Task {
  task_key: string
  title: string
  status: string
  status_display?: string
  priority: string
  assignee_name: string | null
  assignee_email: string | null
  due_date: string | null
  url: string
  parent_key: string | null
  issue_type: string
  provider_type?: string | null
  work_item_type?: string | null
  issue_type_icon_url?: string | null
  epic_key?: string
  epic_title?: string
}

interface HealthNode {
  key: string
  title: string
  issue_type: string
  provider_type?: string | null
  work_item_type?: string | null
  issue_type_icon_url?: string | null
  health_status: string
  health_reason: string
  health_action: string
  parent_key?: string | null
  assignee?: string | null
  due_date?: string | null
}

const normalizeHealthStatus = (value?: string | null): "on_track" | "at_risk" | "critical" | "" => {
  const normalized = String(value || "")
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/-/g, "_")
  if (normalized === "on_track" || normalized === "at_risk" || normalized === "critical") return normalized
  return ""
}

interface FocusContext {
  focusType: "epic" | "story" | "task" | null
  focusKey: string | null
  focusParentKey: string | null
  focusSeverity: string | null
  fromPMBoard: boolean
}

const renderIssueIcon = (
  descriptor: { issueType?: string | null; providerType?: string | null; workItemType?: string | null; issueTypeIconUrl?: string | null },
  label?: string,
  dm = false
) => {
  const filter = dm ? { filter: "brightness(0) invert(1)" } : undefined
  return (
    <span style={filter}>
      {renderWorkItemIcon(
        {
          issueType: descriptor.issueType,
          providerType: descriptor.providerType,
          workItemType: descriptor.workItemType,
          issueTypeIconUrl: descriptor.issueTypeIconUrl,
        },
        label || "Task"
      )}
    </span>
  )
}

export default function ProjectHealthHierarchy({
  projectId,
  mongoId,
  backendUrl,
  refreshSignal = 0,
  onRefreshStateChange,
  initialHealthData = null,
  fallbackHealthStatus = null,
  onConsumeFallbackHealthStatus,
  recommendationByTaskKey = {},
  recommendationAlerts = [],
  isDarkMode = false,
  recPanelOpen = false,
  onRecPanelClose,
  onRecommendationCountChange,
}: ProjectHealthHierarchyProps) {
  const dm = isDarkMode
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const { data: session } = useSession()
  const backendToken = String(session?.user?.backendToken || "").trim()

  const [healthData, setHealthData] = useState<any>(null)
  const [allTasks, setAllTasks] = useState<Task[]>([])
  const [filteredTasks, setFilteredTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [manualRefreshing, setManualRefreshing] = useState(false)

  const [nameFilter, setNameFilter] = useState("")
  const [assigneeFilter, setAssigneeFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const [dueDateFilter, setDueDateFilter] = useState("")
  const [healthStatusFilter, setHealthStatusFilter] = useState<"on_track" | "at_risk" | "critical" | null>(null)
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set())
  const [isExpandAllActive, setIsExpandAllActive] = useState(false)
  const [activeRecommendationGroupKey, setActiveRecommendationGroupKey] = useState<string | null>(null)
  const workItemView = String(searchParams.get("workItemView") || "").toLowerCase()
  const quickFocus = String(searchParams.get("quickFocus") || "").toLowerCase()
  const recommendationCategoryParam = String(searchParams.get("recommendationCategory") || "").toLowerCase()
  const [activeRecommendationCategory, setActiveRecommendationCategory] = useState<"work_item" | "best_practice">("work_item")
  const suppressAutoExpandRef = React.useRef(false)

  const hydrateHealthData = useCallback((healthJson: any) => {
    setHealthData(healthJson)
    const workItems = Array.isArray(healthJson?.work_items) ? healthJson.work_items : []
    const allTasksData: Task[] = workItems
      .filter((item: any) => String(item?.issue_type || "").toLowerCase() !== "epic")
      .map((item: any) => ({
        task_key: String(item.task_key || ""),
        title: String(item.title || item.task_key || ""),
        status: String(item.status || ""),
        status_display: String(item.status_display || item.status_raw || item.status || ""),
        priority: String(item.priority || ""),
        assignee_name: item.assignee_name || item.assignee || null,
        assignee_email: item.assignee_email || null,
        due_date: item.due_date || null,
        url: item.url || "",
        parent_key: item.parent_key || null,
        issue_type: String(item.issue_type || "task"),
        provider_type: item.provider_type || null,
        work_item_type: item.work_item_type || null,
        issue_type_icon_url: item.issue_type_icon_url || null,
        epic_key: item.epic_key || null,
        epic_title: item.epic_title || undefined,
      }))
    setAllTasks(allTasksData)
    setFilteredTasks(allTasksData)
  }, [])

  const clearAllFilters = () => {
    suppressAutoExpandRef.current = true
    setNameFilter("")
    setAssigneeFilter("")
    setStatusFilter("")
    setDueDateFilter("")
    setHealthStatusFilter(null)
    setActiveRecommendationGroupKey(null)
    const params = new URLSearchParams(searchParams.toString())
    params.delete("healthStatus")
    params.delete("quickFocus")
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }

  const hasUsableInitialHealthData = useMemo(() => {
    if (!initialHealthData || !Array.isArray(initialHealthData?.work_items)) return false

    const items = initialHealthData.work_items
    const calculatedAtRaw = String(initialHealthData?.calculated_at || "").trim()
    const calculatedAtMs = calculatedAtRaw ? Date.parse(calculatedAtRaw) : Number.NaN
    const isFresh =
      Number.isFinite(calculatedAtMs) && (Date.now() - calculatedAtMs) <= 2 * 60 * 1000

    // Non-empty initial payload is only usable when it is fresh.
    if (items.length > 0) return isFresh

    // For valid "no work items" projects, keep using initial data only when it is fresh.
    const summaryTotal = Number(
      initialHealthData?.work_item_summary?.total ??
      initialHealthData?.summary?.total_tasks ??
      0
    )

    return summaryTotal === 0 && isFresh
  }, [initialHealthData])

  useEffect(() => {
    if (!hasUsableInitialHealthData) return
    hydrateHealthData(initialHealthData)
    setLoading(false)
  }, [hasUsableInitialHealthData, initialHealthData, hydrateHealthData])

  useEffect(() => {
    if (!projectId) return
    if (hasUsableInitialHealthData) return

    let cancelled = false
    const fetchData = async () => {
      try {
        setLoading(true)
        const healthResponse = await fetch(`${backendUrl}/api/projects/${projectId}/project-health`, {
          headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
        })
        if (!healthResponse.ok) return
        const healthJson = await healthResponse.json()
        if (cancelled) return
        hydrateHealthData(healthJson)
      } catch (error) {
        if (!cancelled) {
          console.error("Error fetching project health hierarchy:", error)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchData()
    return () => {
      cancelled = true
    }
  }, [projectId, backendUrl, hasUsableInitialHealthData, hydrateHealthData, backendToken])

  const refreshHealthData = useCallback(async () => {
    if (!projectId) return
    try {
      setManualRefreshing(true)
      onRefreshStateChange?.(true)
      const healthResponse = await fetch(`${backendUrl}/api/projects/${projectId}/project-health`, {
        headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
        cache: "no-store",
      })
      if (!healthResponse.ok) {
        console.error("Failed to refresh project health hierarchy:", healthResponse.status)
        return
      }
      const healthJson = await healthResponse.json()
      hydrateHealthData(healthJson)
    } catch (error) {
      console.error("Error refreshing project health hierarchy:", error)
    } finally {
      setManualRefreshing(false)
      onRefreshStateChange?.(false)
    }
  }, [projectId, backendUrl, backendToken, hydrateHealthData, onRefreshStateChange])

  useEffect(() => {
    if (loading && onRefreshStateChange) {
      onRefreshStateChange(true)
      return
    }
    if (!loading && !manualRefreshing && onRefreshStateChange) {
      onRefreshStateChange(false)
    }
  }, [loading, manualRefreshing, onRefreshStateChange])

  useEffect(() => {
    if (!projectId) return
    if (refreshSignal <= 0) return
    void refreshHealthData()
  }, [projectId, refreshSignal, refreshHealthData])

  useEffect(() => {
    let filtered = [...allTasks]
    const isTodayFocus = quickFocus === "today_focus"

    const parseYmd = (value?: string | null) => {
      const raw = String(value || "").trim()
      if (!raw) return null
      const ymd = raw.slice(0, 10)
      if (!/^\d{4}-\d{2}-\d{2}$/.test(ymd)) return null
      return ymd
    }

    if (isTodayFocus) {
      const now = new Date()
      const todayYmd = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`
      filtered = filtered.filter((t) => {
        if (isCompletedStatus(t.status)) return false
        const dueYmd = parseYmd(t.due_date)
        if (!dueYmd) return false
        return dueYmd <= todayYmd
      })
    }

    if (nameFilter) {
      const q = nameFilter.toLowerCase()
      filtered = filtered.filter((t) => t.title.toLowerCase().includes(q) || t.task_key.toLowerCase().includes(q))
    }

    if (assigneeFilter) {
      const q = assigneeFilter.toLowerCase()
      filtered = filtered.filter((t) => (t.assignee_name || "").toLowerCase().includes(q))
    }

    if (statusFilter) {
      const q = statusFilter.toLowerCase()
      filtered = filtered.filter((t) => t.status.toLowerCase().includes(q))
    }

    if (dueDateFilter) {
      filtered = filtered.filter((t) => t.due_date && t.due_date.includes(dueDateFilter))
    }

    setFilteredTasks(filtered)
  }, [nameFilter, assigneeFilter, statusFilter, dueDateFilter, allTasks, quickFocus])

  const focusContext: FocusContext = useMemo(() => {
    const focusTypeRaw = searchParams.get("focusType")
    const focusType = focusTypeRaw === "epic" || focusTypeRaw === "story" || focusTypeRaw === "task" ? focusTypeRaw : null
    return {
      focusType,
      focusKey: searchParams.get("focusKey"),
      focusParentKey: searchParams.get("focusParentKey"),
      focusSeverity: searchParams.get("focusSeverity"),
      fromPMBoard: searchParams.get("from") === "pm-board",
    }
  }, [searchParams])

  useEffect(() => {
    const raw = normalizeHealthStatus(searchParams.get("healthStatus"))
    if (raw) {
      setHealthStatusFilter(raw)
      if (onConsumeFallbackHealthStatus) onConsumeFallbackHealthStatus()
      return
    }
    if (fallbackHealthStatus) {
      setHealthStatusFilter(fallbackHealthStatus)
      const params = new URLSearchParams(searchParams.toString())
      params.set("healthStatus", fallbackHealthStatus)
      router.replace(`${pathname}?${params.toString()}`, { scroll: false })
      if (onConsumeFallbackHealthStatus) onConsumeFallbackHealthStatus()
      return
    }
    setHealthStatusFilter(null)
  }, [searchParams, fallbackHealthStatus, onConsumeFallbackHealthStatus, pathname, router])

  useEffect(() => {
    if (workItemView !== "recommendations") return
    setActiveRecommendationGroupKey(null)
  }, [workItemView])

  useEffect(() => {
    if (recommendationCategoryParam === "best_practice") {
      setActiveRecommendationCategory("best_practice")
      return
    }
    if (recommendationCategoryParam === "work_item") {
      setActiveRecommendationCategory("work_item")
      return
    }
    setActiveRecommendationCategory("work_item")
  }, [recommendationCategoryParam])

  const healthByKey = useMemo(() => {
    const map = new Map<string, HealthNode>()

    if (Array.isArray(healthData?.work_items) && healthData.work_items.length > 0) {
      healthData.work_items.forEach((item: any) => {
        const key = String(item.task_key || "")
        if (!key) return
        map.set(key, {
          key,
          title: item.title || key,
          issue_type: String(item.issue_type || "task").toLowerCase(),
          provider_type: String(item.provider_type || item.issue_type || "task"),
          work_item_type: String(item.work_item_type || ""),
          issue_type_icon_url: String(item.issue_type_icon_url || ""),
          health_status: item.health_status || "",
          health_reason: item.health_reason || "",
          health_action: item.health_action || "",
          parent_key: item.parent_key || null,
          assignee: item.assignee_name || item.assignee || null,
          due_date: item.due_date || null,
        })
      })
      return map
    }

    if (Array.isArray(healthData?.epics)) {
      healthData.epics.forEach((epic: any) => {
        map.set(epic.epic_key, {
          key: epic.epic_key,
          title: epic.title || epic.epic_key,
          issue_type: "epic",
          provider_type: "Epic",
          work_item_type: "portfolio",
          issue_type_icon_url: String(epic.issue_type_icon_url || ""),
          health_status: epic.health_status || "",
          health_reason: epic.health_reason || "",
          health_action: epic.health_action || "",
        })

        ;(epic.stories || []).forEach((child: any) => {
          map.set(child.task_key, {
            key: child.task_key,
            title: child.title || child.task_key,
            issue_type: String(child.issue_type || "task").toLowerCase(),
            provider_type: String(child.provider_type || child.issue_type || "task"),
            work_item_type: String(child.work_item_type || ""),
            issue_type_icon_url: String(child.issue_type_icon_url || ""),
            health_status: child.health_status || "",
            health_reason: child.health_reason || "",
            health_action: child.health_action || "",
            parent_key: epic.epic_key,
            assignee: child.assignee || null,
            due_date: child.due_date || null,
          })
        })
      })
    } else if (Array.isArray(healthData?.tasks)) {
      healthData.tasks.forEach((task: any) => {
        map.set(task.task_key, {
          key: task.task_key,
          title: task.title || task.task_key,
          issue_type: String(task.issue_type || "task").toLowerCase(),
          provider_type: String(task.provider_type || task.issue_type || "task"),
          work_item_type: String(task.work_item_type || ""),
          issue_type_icon_url: String(task.issue_type_icon_url || ""),
          health_status: task.health_status || "",
          health_reason: task.health_reason || "",
          health_action: task.health_action || "",
          assignee: task.assignee || null,
          due_date: task.due_date || null,
        })
      })
    }

    return map
  }, [healthData])

  const focusedNode = useMemo(() => {
    if (!focusContext.focusKey) return null
    return healthByKey.get(focusContext.focusKey) || null
  }, [focusContext.focusKey, healthByKey])

  const focusedTask = useMemo(() => {
    if (!focusContext.focusKey) return null
    return allTasks.find((t) => t.task_key === focusContext.focusKey) || null
  }, [allTasks, focusContext.focusKey])

  const contextTaskKeys = useMemo(() => {
    if (!focusContext.focusKey || !focusContext.focusType) return null

    if (focusContext.focusType === "epic") {
      return new Set(allTasks.filter((t) => t.epic_key === focusContext.focusKey).map((t) => t.task_key))
    }

    const childrenByParent = new Map<string, string[]>()
    allTasks.forEach((task) => {
      if (!task.parent_key) return
      if (!childrenByParent.has(task.parent_key)) childrenByParent.set(task.parent_key, [])
      childrenByParent.get(task.parent_key)!.push(task.task_key)
    })

    const visited = new Set<string>()
    const queue: string[] = [focusContext.focusKey]
    while (queue.length > 0) {
      const current = queue.shift()!
      if (visited.has(current)) continue
      visited.add(current)
      ;(childrenByParent.get(current) || []).forEach((childKey) => queue.push(childKey))
    }
    return visited
  }, [allTasks, focusContext.focusKey, focusContext.focusType])

  const contextFilteredTasks = useMemo(() => {
    if (!contextTaskKeys) return filteredTasks
    return filteredTasks.filter((t) => contextTaskKeys.has(t.task_key))
  }, [filteredTasks, contextTaskKeys])

  const relatedRisks = useMemo(() => {
    if (!focusContext.focusKey) return []
    const source = Array.from(healthByKey.values())
    const critical = source.filter((n) => n.health_status === "critical" && n.key !== focusContext.focusKey)
    if (!focusedTask?.epic_key) return critical.slice(0, 5)
    const sameEpic = critical.filter((n) => {
      const task = allTasks.find((t) => t.task_key === n.key)
      return task?.epic_key && task.epic_key === focusedTask.epic_key
    })
    return (sameEpic.length > 0 ? sameEpic : critical).slice(0, 5)
  }, [focusContext.focusKey, healthByKey, allTasks, focusedTask?.epic_key])

  const clearContext = () => {
    const params = new URLSearchParams(searchParams.toString())
    params.delete("focusType")
    params.delete("focusKey")
    params.delete("focusParentKey")
    params.delete("focusSeverity")
    params.delete("from")
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }

  const toggleExpand = (key: string) => {
    setExpandedItems((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const buildHierarchy = (tasks: Task[]) => {
    const epics = new Map<string, { key: string; title: string; children: Task[] }>()
    const ungrouped: Task[] = []
    const hasEpicHierarchy = Array.isArray(healthData?.epics) && healthData.epics.length > 0

    tasks.forEach((task) => {
      if (hasEpicHierarchy && task.epic_key) {
        if (!epics.has(task.epic_key)) {
          epics.set(task.epic_key, { key: task.epic_key, title: task.epic_title || task.epic_key, children: [] })
        }
        epics.get(task.epic_key)!.children.push(task)
      } else {
        ungrouped.push(task)
      }
    })

    const result = Array.from(epics.values())
    if (ungrouped.length > 0) {
      result.push({ key: "__ungrouped__", title: "Tasks", children: ungrouped })
    }
    return result
  }

  const getChildren = (parentKey: string, tasks: Task[]) => tasks.filter((t) => t.parent_key === parentKey)

  const getHierarchySourceTasks = (tasks: Task[]) => {
    if (!healthStatusFilter) return tasks

    const byKey = new Map<string, Task>()
    allTasks.forEach((task) => byKey.set(task.task_key, task))
    tasks.forEach((task) => byKey.set(task.task_key, task))

    const result = new Map<string, Task>()
    tasks.forEach((task) => {
      result.set(task.task_key, task)
      let parentKey = task.parent_key
      const visited = new Set<string>()
      while (parentKey && !visited.has(parentKey)) {
        visited.add(parentKey)
        const parent = byKey.get(parentKey)
        if (!parent) break
        result.set(parent.task_key, parent)
        parentKey = parent.parent_key
      }
    })
    return Array.from(result.values())
  }

  const getStatusChipClass = (status: string) => {
    const normalized = String(status || "").toLowerCase().replace(/\s+/g, "_")
    if (["done", "closed", "resolved", "completed"].includes(normalized)) return dm ? "bg-green-900/40 text-green-300" : "bg-green-100 text-green-700"
    if (["in_progress", "in-progress"].includes(normalized)) return dm ? "bg-blue-900/40 text-blue-300" : "bg-blue-100 text-blue-700"
    if (["to_do", "open", "backlog"].includes(normalized)) return dm ? "bg-white/[0.08] text-gray-300" : "bg-gray-100 text-gray-700"
    return dm ? "bg-white/[0.08] text-gray-300" : "bg-gray-100 text-gray-700"
  }

  const getHealthChipClass = (healthStatus: string) => {
    if (healthStatus === "critical") return dm ? "bg-rose-900/40 text-rose-300" : "bg-rose-100 text-rose-700"
    if (healthStatus === "at_risk") return dm ? "bg-amber-900/40 text-amber-300" : "bg-amber-100 text-amber-700"
    if (healthStatus === "on_track") return dm ? "bg-green-900/40 text-green-300" : "bg-green-100 text-green-700"
    return dm ? "bg-white/[0.08] text-gray-300" : "bg-gray-100 text-gray-600"
  }

  const getRecommendationText = (taskKey?: string, healthStatus?: string, healthAction?: string) => {
    const unifiedRecommendation = taskKey ? recommendationByTaskKey[taskKey] : null
    if (unifiedRecommendation) return unifiedRecommendation
    if (!healthStatus || !healthAction) return null
    if (healthStatus === "critical" || healthStatus === "at_risk") return healthAction
    return null
  }

  const isCompletedStatus = (status?: string) => {
    const normalized = String(status || "").toLowerCase().replace(/\s+/g, "_")
    return ["done", "closed", "resolved", "completed"].includes(normalized)
  }

  const healthStatusFilteredTasks = useMemo(() => {
    if (!healthStatusFilter) return contextFilteredTasks
    return contextFilteredTasks.filter((task) => {
      if (isCompletedStatus(task.status)) return false
      const status = normalizeHealthStatus(healthByKey.get(task.task_key)?.health_status)
      return status === healthStatusFilter
    })
  }, [contextFilteredTasks, healthStatusFilter, healthByKey])

  const recommendationScopeTaskKeys = useMemo(() => {
    if (contextTaskKeys) return contextTaskKeys
    return new Set(allTasks.map((task) => task.task_key).filter(Boolean))
  }, [contextTaskKeys, allTasks])

  const taskByKey = useMemo(() => {
    const map = new Map<string, Task>()
    allTasks.forEach((task) => {
      if (!task.task_key) return
      if (!map.has(task.task_key)) map.set(task.task_key, task)
    })
    return map
  }, [allTasks])

  const groupedRecommendations = useMemo(() => {
    const inScopeKeys = recommendationScopeTaskKeys
    type RecommendationGroup = {
      key: string
      recommendation: string
      category: "work_item" | "best_practice"
      alerts: RecommendationAlert[]
      taskKeys: string[]
      severityMix: { critical: number; atRisk: number; info: number }
    }

    const groups = new Map<string, RecommendationGroup & { taskKeySet: Set<string> }>()

    recommendationAlerts.forEach((alert) => {
      const category: "work_item" | "best_practice" = alert.category === "best_practice" ? "best_practice" : "work_item"
      const taskKey = String(alert.taskKey || "").trim()

      if (category === "work_item") {
        if (!taskKey || !inScopeKeys.has(taskKey)) return
      } else if (taskKey && !inScopeKeys.has(taskKey)) {
        return
      }

      const normalizedRecommendation = String(alert.recommendation || "").trim() || "Needs action"
      const groupKey = `${category}::${normalizedRecommendation.toLowerCase()}`
      const existing = groups.get(groupKey)
      if (!existing) {
        const taskKeySet = new Set<string>()
        if (taskKey) taskKeySet.add(taskKey)
        groups.set(groupKey, {
          key: groupKey,
          recommendation: normalizedRecommendation,
          category,
          alerts: [alert],
          taskKeys: taskKey ? [taskKey] : [],
          taskKeySet,
          severityMix: {
            critical: alert.severity === "critical" ? 1 : 0,
            atRisk: alert.severity === "at_risk" ? 1 : 0,
            info: alert.severity === "info" ? 1 : 0,
          },
        })
        return
      }

      existing.alerts.push(alert)
      if (taskKey && !existing.taskKeySet.has(taskKey)) {
        existing.taskKeySet.add(taskKey)
        existing.taskKeys.push(taskKey)
      }
      if (alert.severity === "critical") existing.severityMix.critical += 1
      else if (alert.severity === "at_risk") existing.severityMix.atRisk += 1
      else existing.severityMix.info += 1
    })

    const sorted = Array.from(groups.values())
      .map((group) => ({
        key: group.key,
        recommendation: group.recommendation,
        category: group.category,
        alerts: group.alerts,
        taskKeys: group.taskKeys,
        severityMix: group.severityMix,
      }))
      .sort((a, b) => {
        const aCritical = a.severityMix.critical > 0 ? 1 : 0
        const bCritical = b.severityMix.critical > 0 ? 1 : 0
        if (aCritical !== bCritical) return bCritical - aCritical
        return b.alerts.length - a.alerts.length
      })

    return {
      workItem: sorted.filter((group) => group.category === "work_item"),
      bestPractice: sorted.filter((group) => group.category === "best_practice"),
    }
  }, [recommendationScopeTaskKeys, recommendationAlerts])

  const activeRecommendationTaskKeys = useMemo(() => {
    if (!activeRecommendationGroupKey) return null
    const allGroups = [...groupedRecommendations.workItem, ...groupedRecommendations.bestPractice]
    const group = allGroups.find((item) => item.key === activeRecommendationGroupKey)
    if (!group || group.taskKeys.length === 0) return null
    return new Set(group.taskKeys)
  }, [activeRecommendationGroupKey, groupedRecommendations])

  useEffect(() => {
    if (!activeRecommendationGroupKey) return
    const allGroupKeys = new Set(
      [...groupedRecommendations.workItem, ...groupedRecommendations.bestPractice].map((group) => group.key)
    )
    if (!allGroupKeys.has(activeRecommendationGroupKey)) {
      setActiveRecommendationGroupKey(null)
    }
  }, [activeRecommendationGroupKey, groupedRecommendations])

  const recommendationGroupsFlat = useMemo(
    () => [...groupedRecommendations.workItem, ...groupedRecommendations.bestPractice],
    [groupedRecommendations]
  )

  const activeCategoryGroups = useMemo(
    () => (activeRecommendationCategory === "best_practice" ? groupedRecommendations.bestPractice : groupedRecommendations.workItem),
    [activeRecommendationCategory, groupedRecommendations]
  )

  const activeRecommendationGroup = useMemo(
    () => recommendationGroupsFlat.find((group) => group.key === activeRecommendationGroupKey) || null,
    [recommendationGroupsFlat, activeRecommendationGroupKey]
  )

  useEffect(() => {
    if (!activeRecommendationGroupKey) return
    const scopedKeys = new Set(activeCategoryGroups.map((group) => group.key))
    if (!scopedKeys.has(activeRecommendationGroupKey)) {
      setActiveRecommendationGroupKey(null)
    }
  }, [activeRecommendationGroupKey, activeCategoryGroups])

  useEffect(() => {
    onRecommendationCountChange?.({
      workItem: groupedRecommendations.workItem.length,
      bestPractice: groupedRecommendations.bestPractice.length,
    })
  }, [groupedRecommendations, onRecommendationCountChange])

  const setRecommendationCategory = useCallback((category: "work_item" | "best_practice") => {
    setActiveRecommendationCategory(category)
    const params = new URLSearchParams(searchParams.toString())
    params.set("recommendationCategory", category)
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }, [pathname, router, searchParams])

  const appliedFilterLabels = useMemo(() => {
    const labels: string[] = []
    if (nameFilter.trim()) labels.push(`Key/Name: ${nameFilter.trim()}`)
    if (assigneeFilter.trim()) labels.push(`Assignee: ${assigneeFilter.trim()}`)
    if (statusFilter.trim()) labels.push(`Status: ${statusFilter.trim()}`)
    if (dueDateFilter.trim()) labels.push(`Date: ${dueDateFilter.trim()}`)
    if (healthStatusFilter) {
      labels.push(`Health: ${healthStatusFilter === "on_track" ? "On Track" : healthStatusFilter === "at_risk" ? "At Risk" : "Critical"}`)
    }
    if (activeRecommendationGroup) {
      labels.push(`Recommendation: ${activeRecommendationGroup.recommendation}`)
    }
    if (quickFocus === "today_focus") {
      labels.push("Focus: Today (Overdue + Due Today)")
    }
    return labels
  }, [nameFilter, assigneeFilter, statusFilter, dueDateFilter, healthStatusFilter, activeRecommendationGroup, quickFocus])

  const healthFilteredTasks = useMemo(() => {
    if (!activeRecommendationTaskKeys) return healthStatusFilteredTasks
    return healthStatusFilteredTasks.filter((task) => activeRecommendationTaskKeys.has(task.task_key))
  }, [healthStatusFilteredTasks, activeRecommendationTaskKeys])

  const renderTaskRow = (task: Task, level = 0, sourceTasks: Task[] = healthFilteredTasks) => {
    const children = getChildren(task.task_key, sourceTasks)
    const hasChildren = children.length > 0
    const isExpanded = expandedItems.has(task.task_key)
    const indent = level * 24
    const health = healthByKey.get(task.task_key)
    const isClosed = isCompletedStatus(task.status)
    const childHealthStatuses = children
      .map((child) => healthByKey.get(child.task_key)?.health_status)
      .filter((value): value is string => Boolean(value))
    const derivedChildHealth =
      childHealthStatuses.includes("critical")
        ? "critical"
        : childHealthStatuses.includes("at_risk")
          ? "at_risk"
          : childHealthStatuses.includes("on_track")
            ? "on_track"
            : ""
    const effectiveHealthStatus = health?.health_status || derivedChildHealth
    const issueType = String(health?.issue_type || task.issue_type || "task").toLowerCase()
    const providerTypeLabel = String(health?.provider_type || task.provider_type || issueType || "Task")
    const isFocused = focusContext.focusKey === task.task_key
    const recommendation = getRecommendationText(task.task_key, health?.health_status, health?.health_action)

    return (
      <React.Fragment key={task.task_key}>
        <tr className={`border-b ${dm ? "border-white/[0.06]" : "border-gray-100"} transition ${isFocused ? "bg-[#78a530]/10" : (dm ? "hover:bg-white/[0.04]" : "hover:bg-[#f7fbee]")}`}>
          <td className="px-4 py-3 whitespace-nowrap" style={{ paddingLeft: `${16 + indent}px` }}>
            <div className="flex items-center gap-2">
              {hasChildren ? (
                <button onClick={() => toggleExpand(task.task_key)} className={`${dm ? "text-gray-500 hover:text-gray-300" : "text-gray-400 hover:text-gray-600"}`}>
                  <svg className={`w-4 h-4 transition-transform ${isExpanded ? "rotate-90" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </button>
              ) : (
                <span className="w-4" />
              )}
              <span className="inline-flex items-center w-4 h-4 flex-shrink-0" title={providerTypeLabel}>
                {renderIssueIcon(
                  {
                    issueType,
                    providerType: providerTypeLabel,
                    workItemType: String(health?.work_item_type || task.work_item_type || ""),
                    issueTypeIconUrl: String(health?.issue_type_icon_url || task.issue_type_icon_url || ""),
                  },
                  providerTypeLabel || "Task",
                  dm
                )}
              </span>
              {task.url ? (
                <a
                  href={task.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`text-sm font-mono font-medium ${dm ? "text-gray-200" : "text-gray-700"} hover:underline hover:text-[#78a530]`}
                >
                  {task.task_key}
                </a>
              ) : (
                <span className={`text-sm font-mono font-medium ${dm ? "text-gray-200" : "text-gray-700"}`}>{task.task_key}</span>
              )}
            </div>
          </td>
          <td className="px-4 py-3 max-w-md">
            {task.url ? (
              <a
                href={task.url}
                target="_blank"
                rel="noopener noreferrer"
                className={`text-sm font-medium ${dm ? "text-gray-100" : "text-gray-900"} hover:text-[#78a530] hover:underline`}
              >
                {task.title}
              </a>
            ) : (
              <span className={`text-sm font-medium ${dm ? "text-gray-100" : "text-gray-900"}`}>{task.title}</span>
            )}
          </td>
          <td className="px-4 py-3 whitespace-nowrap">
            {!isClosed && effectiveHealthStatus ? (
              <span className={`text-sm px-2.5 py-1 rounded-full font-semibold ${getHealthChipClass(effectiveHealthStatus)}`}>
                {effectiveHealthStatus === "on_track" ? "On Track" : effectiveHealthStatus === "at_risk" ? "At Risk" : "Critical"}
              </span>
            ) : (
              <span className={`text-sm ${dm ? "text-gray-400" : "text-gray-400"}`}>-</span>
            )}
          </td>
          <td className={`px-4 py-3 text-sm font-medium ${dm ? "text-gray-200" : "text-gray-700"} whitespace-nowrap`}>
            {task.assignee_name || <span className={`italic text-sm ${dm ? "text-gray-500" : "text-gray-400"}`}>Unassigned</span>}
          </td>
          <td className="px-4 py-3 whitespace-nowrap">
            <span className={`text-sm px-2.5 py-1 rounded-full font-semibold ${getStatusChipClass(task.status)}`}>
              {String(task.status_display || task.status || "").replace("_", " ")}
            </span>
          </td>
          <td className={`px-4 py-3 text-sm font-medium ${dm ? "text-gray-200" : "text-gray-700"} whitespace-nowrap`}>
            {task.due_date ? new Date(task.due_date).toLocaleDateString() : <span className={dm ? "text-gray-500" : "text-gray-400"}>-</span>}
          </td>
          <td className="px-4 py-3 text-center">
            {recommendation ? (
              <div className="relative inline-flex items-center justify-center group">
                <button
                  type="button"
                  className={`w-6 h-6 rounded-full border ${dm ? "border-white/[0.08] text-gray-400" : "border-gray-200 text-gray-500"} hover:text-[#78a530] hover:border-[#78a530] transition`}
                  aria-label="View recommendation"
                >
                  <svg className="w-3.5 h-3.5 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </button>
                <div className={`pointer-events-none absolute z-20 right-0 top-7 w-72 p-2 rounded-md border ${dm ? "border-white/[0.08] bg-[#1e2638] text-gray-300" : "border-gray-200 bg-white text-gray-700"} text-left text-xs shadow-lg opacity-0 group-hover:opacity-100 transition-opacity`}>
                  {recommendation}
                </div>
              </div>
            ) : (
              <span className={`${dm ? "text-gray-600" : "text-gray-300"}`}>-</span>
            )}
          </td>
        </tr>
        {isExpanded && children.map((child) => renderTaskRow(child, level + 1, sourceTasks))}
      </React.Fragment>
    )
  }

  const renderEpicRow = (epic: { key: string; title: string; children: Task[] }) => {
    const isExpanded = expandedItems.has(epic.key)
    const rootTasks = epic.children.filter((t) => !t.parent_key || t.parent_key === epic.key)
    const isUngrouped = epic.key === "__ungrouped__"
    const epicHealth = isUngrouped ? null : healthByKey.get(epic.key)
    const epicTask = taskByKey.get(epic.key)
    const epicIsClosed = epicTask ? isCompletedStatus(epicTask.status) : false
    const childHealthStatuses = epic.children
      .map((child) => healthByKey.get(child.task_key)?.health_status)
      .filter((value): value is string => Boolean(value))
    const derivedEpicHealth =
      childHealthStatuses.includes("critical")
        ? "critical"
        : childHealthStatuses.includes("at_risk")
          ? "at_risk"
          : childHealthStatuses.includes("on_track")
            ? "on_track"
            : ""
    const effectiveEpicHealth = epicHealth?.health_status || derivedEpicHealth

    return (
      <React.Fragment key={epic.key}>
        {isUngrouped ? (
          <tr className={`${dm ? "bg-white/[0.04] border-white/[0.10]" : "bg-gray-50 border-gray-300"} border-b-2`}>
            <td colSpan={7} className="px-4 py-3">
              <button onClick={() => toggleExpand(epic.key)} className="flex items-center gap-3 w-full text-left">
                <svg className={`w-4 h-4 ${dm ? "text-gray-400" : "text-gray-500"} transition-transform ${isExpanded ? "rotate-90" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                <span className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>{epic.title}</span>
                <span className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>({epic.children.length} items)</span>
              </button>
            </td>
          </tr>
        ) : (
          <tr className={`border-b-2 ${dm ? "border-white/[0.10]" : "border-[#78a530]/20"}`} style={dm ? { background: 'rgba(255,255,255,0.04)' } : { background: 'linear-gradient(90deg, rgba(120,165,48,0.06) 0%, rgba(240,249,224,0.4) 100%)' }}>
            <td className="px-4 py-3 whitespace-nowrap">
              <button onClick={() => toggleExpand(epic.key)} className="flex items-center gap-2 text-left">
                <svg className={`w-4 h-4 ${dm ? "text-gray-400" : "text-gray-500"} transition-transform ${isExpanded ? "rotate-90" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                <span className="inline-flex items-center w-4 h-4 flex-shrink-0" title="Epic">
                  {renderIssueIcon({ issueType: "epic", providerType: "Epic", workItemType: "portfolio" }, "Epic", dm)}
                </span>
                <span className={`text-sm font-mono font-semibold ${dm ? "text-gray-200" : "text-gray-800"}`}>{epic.key}</span>
              </button>
            </td>
            <td className="px-4 py-3">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"} truncate`}>{epic.title}</span>
                <span className={`text-xs whitespace-nowrap ${dm ? "text-gray-400" : "text-gray-500"}`}>({epic.children.length} items)</span>
              </div>
            </td>
            <td className="px-4 py-3 whitespace-nowrap">
              {!epicIsClosed && effectiveEpicHealth ? (
                <span className={`text-sm px-2.5 py-1 rounded-full font-semibold ${getHealthChipClass(effectiveEpicHealth)}`}>
                  {effectiveEpicHealth === "on_track" ? "On Track" : effectiveEpicHealth === "at_risk" ? "At Risk" : "Critical"}
                </span>
              ) : (
                <span className={`text-sm ${dm ? "text-gray-500" : "text-gray-400"}`}>-</span>
              )}
            </td>
            <td className={`px-4 py-3 text-sm ${dm ? "text-gray-500" : "text-gray-400"}`}>-</td>
            <td className={`px-4 py-3 text-sm ${dm ? "text-gray-500" : "text-gray-400"}`}>-</td>
            <td className={`px-4 py-3 text-sm ${dm ? "text-gray-500" : "text-gray-400"}`}>-</td>
            <td className={`px-4 py-3 text-center ${dm ? "text-gray-500" : "text-gray-300"}`}>-</td>
          </tr>
        )}
        {isExpanded && rootTasks.map((task) => renderTaskRow(task, 0, hierarchySourceTasks))}
      </React.Fragment>
    )
  }

  const hierarchySourceTasks = useMemo(
    () => getHierarchySourceTasks(healthFilteredTasks),
    [healthFilteredTasks, allTasks, healthStatusFilter]
  )
  const hierarchy = useMemo(
    () => buildHierarchy(hierarchySourceTasks),
    [hierarchySourceTasks]
  )
  const allExpandableKeys = useMemo(() => {
    const keys = new Set<string>()
    hierarchy.forEach((group) => {
      keys.add(group.key)
    })
    hierarchySourceTasks.forEach((task) => {
      if (getChildren(task.task_key, hierarchySourceTasks).length > 0) {
        keys.add(task.task_key)
      }
    })
    return Array.from(keys)
  }, [hierarchy, hierarchySourceTasks])
  const hasActiveTableFilters = useMemo(() => {
    return Boolean(
      nameFilter.trim() ||
      assigneeFilter.trim() ||
      statusFilter.trim() ||
      dueDateFilter.trim() ||
      healthStatusFilter ||
      activeRecommendationGroupKey ||
      quickFocus === "today_focus"
    )
  }, [
    nameFilter,
    assigneeFilter,
    statusFilter,
    dueDateFilter,
    healthStatusFilter,
    activeRecommendationGroupKey,
    quickFocus,
  ])

  useEffect(() => {
    if (!hasActiveTableFilters) {
      suppressAutoExpandRef.current = false
    }
  }, [hasActiveTableFilters])

  useEffect(() => {
    if (suppressAutoExpandRef.current) return
    if (!hasActiveTableFilters || allExpandableKeys.length === 0) return
    setIsExpandAllActive(true)
    setExpandedItems((prev) => {
      const missingKeys = allExpandableKeys.filter((key) => !prev.has(key))
      if (missingKeys.length === 0) return prev
      const next = new Set(prev)
      missingKeys.forEach((key) => next.add(key))
      return next
    })
  }, [hasActiveTableFilters, allExpandableKeys])

  const toggleExpandCollapseAll = () => {
    if (isExpandAllActive) {
      setExpandedItems(new Set())
      setIsExpandAllActive(false)
      return
    }
    setExpandedItems((prev) => {
      const next = new Set(prev)
      allExpandableKeys.forEach((key) => next.add(key))
      return next
    })
    setIsExpandAllActive(true)
  }

  if (loading) {
    return (
      <div className="p-6 text-center">
        <img src="/logos/loading.gif" alt="Loading..." className="w-8 h-8 mx-auto" />
        <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"} mt-2`}>Loading project health...</p>
      </div>
    )
  }

  return (
    <div className="relative px-6 pt-1 pb-6 space-y-4">
      {focusContext.focusKey && (
        <div className={`${dm ? "bg-[#1e2638] border-white/[0.08]" : "bg-white border-gray-200"} rounded-lg border shadow-sm overflow-hidden`}>
          <div className={`px-4 py-3 border-b ${dm ? "border-white/[0.06]" : "border-gray-100"} flex flex-wrap items-center gap-2 justify-between`} style={{ background: dm ? "linear-gradient(to right, rgba(120, 165, 48, 0.08), transparent)" : "linear-gradient(to right, rgba(120, 165, 48, 0.08), white)" }}>
            <div className={`flex items-center gap-2 text-sm ${dm ? "text-gray-300" : "text-gray-700"} min-w-0`}>
              {focusContext.fromPMBoard && <span className="text-xs font-semibold text-[#78a530]">From PM Board</span>}
              <span className={dm ? "text-gray-500" : "text-gray-400"}>/</span>
              <span className="inline-flex items-center gap-1.5 font-medium capitalize">
                <span className="inline-flex items-center w-4 h-4 flex-shrink-0">
                  {renderIssueIcon(
                    {
                      issueType: focusContext.focusType || "task",
                      providerType: focusContext.focusType || "Item",
                      workItemType: focusContext.focusType === "epic" ? "portfolio" : "",
                    },
                    focusContext.focusType || "Item",
                    dm
                  )}
                </span>
                {focusContext.focusType || "item"}
              </span>
              <span className={dm ? "text-gray-500" : "text-gray-400"}>/</span>
              <span className="font-mono text-xs">{focusContext.focusKey}</span>
              {(focusedNode?.title || focusedTask?.title) && (
                <>
                  <span className={dm ? "text-gray-500" : "text-gray-400"}>-</span>
                  <span className="truncate">{focusedNode?.title || focusedTask?.title}</span>
                </>
              )}
            </div>
            <button onClick={clearContext} className="text-xs text-[#78a530] font-semibold hover:underline">
              Clear Context
            </button>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 p-4">
            <div className="xl:col-span-2 space-y-3">
              <div className={`rounded-lg border ${dm ? "border-white/[0.08]" : "border-gray-200"} p-3`}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs uppercase tracking-wide text-gray-500">Focused Detail</span>
                  {focusedNode?.health_status && (
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${getHealthChipClass(focusedNode.health_status)}`}>
                      {focusedNode.health_status === "on_track" ? "On Track" : focusedNode.health_status === "at_risk" ? "At Risk" : "Critical"}
                    </span>
                  )}
                </div>
                <p className={`text-sm ${dm ? "text-gray-100" : "text-gray-900"} mt-2`}>
                  {focusedNode?.health_reason || "No explicit health reason available for this item."}
                </p>
              </div>
              <div className={`rounded-lg border ${dm ? "border-white/[0.08]" : "border-gray-200"} p-3`}>
                <span className="text-xs uppercase tracking-wide text-gray-500">Recommended Action</span>
                <p className={`text-sm ${dm ? "text-gray-100" : "text-gray-900"} mt-2`}>
                  {getRecommendationText(focusedNode?.key, focusedNode?.health_status, focusedNode?.health_action) || "No action recommendation available."}
                </p>
              </div>
            </div>

            <div className={`rounded-lg border ${dm ? "border-white/[0.08]" : "border-gray-200"} p-3`}>
              <span className="text-xs uppercase tracking-wide text-gray-500">Related Risks</span>
              <div className="mt-2 space-y-2">
                {relatedRisks.length === 0 && <p className="text-sm text-gray-500">No related critical risks found.</p>}
                {relatedRisks.map((risk) => (
                  <div key={risk.key} className={`border ${dm ? "border-white/[0.06]" : "border-gray-100"} rounded p-2`}>
                    <p className="text-xs font-mono text-gray-500">{risk.key}</p>
                    <p className={`text-sm ${dm ? "text-gray-100" : "text-gray-900"} truncate`}>{risk.title}</p>
                    <p className="text-xs text-gray-500 mt-1">{risk.health_reason || "Needs attention."}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {recPanelOpen && (
        <>
          <div className="fixed inset-0 z-20" onClick={onRecPanelClose} />
          <div
            className={`absolute top-0 right-0 z-30 w-[360px] flex flex-col rounded-xl border shadow-2xl ${
              dm
                ? "border-white/[0.12] bg-[#1e2638]"
                : "border-gray-200 bg-white"
            }`}
            style={{ maxHeight: "min(480px, 80vh)" }}
          >
            {/* Header */}
            <div className={`flex items-center justify-between px-4 py-3 border-b flex-shrink-0 ${dm ? "border-white/[0.08]" : "border-gray-100"}`}>
              <div className="flex items-center gap-4">
                <button
                  type="button"
                  onClick={() => setRecommendationCategory("work_item")}
                  className={`text-xs font-semibold uppercase tracking-wide pb-1 border-b-2 transition ${
                    activeRecommendationCategory === "work_item"
                      ? "text-[#78a530] border-[#78a530]"
                      : `${dm ? "text-gray-400 hover:text-gray-200" : "text-gray-500 hover:text-gray-700"} border-transparent`
                  }`}
                >
                  Work Item ({groupedRecommendations.workItem.length})
                </button>
                <button
                  type="button"
                  onClick={() => setRecommendationCategory("best_practice")}
                  className={`text-xs font-semibold uppercase tracking-wide pb-1 border-b-2 transition ${
                    activeRecommendationCategory === "best_practice"
                      ? "text-[#78a530] border-[#78a530]"
                      : `${dm ? "text-gray-400 hover:text-gray-200" : "text-gray-500 hover:text-gray-700"} border-transparent`
                  }`}
                >
                  Best Practice ({groupedRecommendations.bestPractice.length})
                </button>
              </div>
              <button
                type="button"
                onClick={onRecPanelClose}
                className={`rounded p-1 transition ${dm ? "text-gray-400 hover:text-gray-200 hover:bg-white/[0.08]" : "text-gray-400 hover:text-gray-700 hover:bg-gray-100"}`}
                aria-label="Close"
              >
                <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M3 3l10 10M13 3L3 13" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            {/* Items */}
            <div className="overflow-y-auto p-3">
              {activeCategoryGroups.length === 0 ? (
                <p className={`text-sm p-2 ${dm ? "text-gray-500" : "text-gray-400"}`}>No recommendations to show.</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {activeCategoryGroups.map((group) => {
                    const isActive = activeRecommendationGroupKey === group.key
                    const canFilterTasks = group.taskKeys.length > 0
                    return (
                      <button
                        key={group.key}
                        type="button"
                        disabled={!canFilterTasks}
                        onClick={() => {
                          if (!canFilterTasks) return
                          setActiveRecommendationGroupKey((prev) => (prev === group.key ? null : group.key))
                        }}
                        className={`text-left rounded-lg border px-3 py-2.5 transition ${
                          isActive
                            ? dm
                              ? "border-[#78a530]/60 bg-[#78a530]/10"
                              : "border-[#78a530] bg-[#f7fbee]"
                            : dm
                              ? "border-white/[0.08] bg-white/[0.03] hover:border-[#78a530]/40 hover:bg-white/[0.06]"
                              : "border-gray-200 bg-white hover:border-[#78a530]/60"
                        } ${canFilterTasks ? "" : "opacity-60 cursor-default"}`}
                      >
                        <p className={`text-sm font-medium line-clamp-2 ${dm ? "text-gray-200" : "text-gray-800"}`}>{group.recommendation}</p>
                        <div className="flex items-center gap-1.5 mt-1">
                          <p className="text-sm font-bold text-[#78a530]">{group.alerts.length}</p>
                          <p className={`text-xs ${dm ? "text-gray-500" : "text-gray-400"}`}>{canFilterTasks ? "tasks" : "project-level"}</p>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </>
      )}

      <div className="flex items-center gap-2 flex-wrap min-h-[28px]">
        {appliedFilterLabels.length > 0 && (
          <>
            <span className={`text-xs font-bold uppercase tracking-wider ${dm ? "text-gray-400" : "text-gray-500"}`}>Active Filters</span>
            {appliedFilterLabels.map((label, i) => {
              const isCritical = label.includes("Critical")
              const isAtRisk = label.includes("At Risk")
              const isOnTrack = label.includes("On Track")
              const badgeStyle = dm
                ? isCritical
                  ? { background: 'rgba(244,63,94,0.15)', color: '#fb7185', border: '1px solid rgba(244,63,94,0.35)' }
                  : isAtRisk
                    ? { background: 'rgba(245,158,11,0.15)', color: '#fbbf24', border: '1px solid rgba(245,158,11,0.35)' }
                    : isOnTrack
                      ? { background: 'rgba(16,185,129,0.15)', color: '#34d399', border: '1px solid rgba(16,185,129,0.35)' }
                      : { background: 'rgba(120,165,48,0.15)', color: '#78a530', border: '1px solid rgba(120,165,48,0.30)' }
                : isCritical
                  ? { background: '#fff1f2', color: '#e11d48', border: '1px solid rgba(244,63,94,0.35)' }
                  : isAtRisk
                    ? { background: '#fffbeb', color: '#d97706', border: '1px solid rgba(245,158,11,0.35)' }
                    : isOnTrack
                      ? { background: '#f0fdf4', color: '#16a34a', border: '1px solid rgba(16,185,129,0.35)' }
                      : { background: '#f0f9e0', color: '#5e8224', border: '1px solid rgba(120,165,48,0.35)' }
              return (
                <span key={i} className="inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold" style={badgeStyle}>
                  {label}
                </span>
              )
            })}
          </>
        )}
      </div>

      <div className={`${dm ? "bg-[#0e1521]" : "bg-white border-gray-200"} rounded-lg border shadow-sm overflow-visible`} style={dm ? { background: 'linear-gradient(160deg, #0e1521 0%, #111828 50%, #0c1320 100%)', boxShadow: '0 0 0 1px rgba(255,255,255,0.12), inset 0 1px 0 rgba(255,255,255,0.08), 0 8px 40px rgba(0,0,0,0.60)' } : { background: 'linear-gradient(160deg, #ffffff 0%, #f8faff 100%)', boxShadow: '0 0 0 1px rgba(0,0,0,0.07), inset 0 1px 0 rgba(255,255,255,1), 0 4px 16px rgba(0,0,0,0.07)' }}>
        <div className={`px-4 pt-3 pb-2 border-b ${dm ? "border-white/[0.10]" : "border-gray-200 bg-white"}`} style={dm ? { background: 'linear-gradient(90deg, #0b1019 0%, #0e1521 100%)' } : undefined}>
          <div className={`rounded-xl border p-3`} style={dm ? { border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.02)' } : { border: '1px solid rgba(120,165,48,0.20)', background: 'linear-gradient(135deg, #f7fbee 0%, #ffffff 100%)' }}>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-6 gap-3 items-center">
              <div className="xl:col-span-1">
                <input
                  type="text"
                  placeholder="Filter by Key/Name..."
                  value={nameFilter}
                  onChange={(e) => setNameFilter(e.target.value)}
                  className={`w-full px-3 py-2 text-sm border ${dm ? "border-white/[0.08] bg-[#080c15] text-gray-300 placeholder-gray-600" : "border-gray-300"} rounded-full focus:outline-none focus:ring-1 focus:ring-[#78a530]`}
                />
              </div>
              <div className="xl:col-span-1">
                <input
                  type="text"
                  placeholder="Filter Assignee..."
                  value={assigneeFilter}
                  onChange={(e) => setAssigneeFilter(e.target.value)}
                  className={`w-full px-3 py-2 text-sm border ${dm ? "border-white/[0.08] bg-[#080c15] text-gray-300 placeholder-gray-600" : "border-gray-300"} rounded-full focus:outline-none focus:ring-1 focus:ring-[#78a530]`}
                />
              </div>
              <div className="xl:col-span-1">
                <input
                  type="text"
                  placeholder="Filter Status..."
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className={`w-full px-3 py-2 text-sm border ${dm ? "border-white/[0.08] bg-[#080c15] text-gray-300 placeholder-gray-600" : "border-gray-300"} rounded-full focus:outline-none focus:ring-1 focus:ring-[#78a530]`}
                />
              </div>
              <div className="xl:col-span-1">
                <input
                  type="text"
                  placeholder="Filter Date..."
                  value={dueDateFilter}
                  onChange={(e) => setDueDateFilter(e.target.value)}
                  className={`w-full px-3 py-2 text-sm border ${dm ? "border-white/[0.08] bg-[#080c15] text-gray-300 placeholder-gray-600" : "border-gray-300"} rounded-full focus:outline-none focus:ring-1 focus:ring-[#78a530]`}
                />
              </div>
              <div className="xl:col-span-1">
                <select
                  value={healthStatusFilter || ""}
                  onChange={(e) => {
                    const nextHealth = normalizeHealthStatus(e.target.value) || null
                    setHealthStatusFilter(nextHealth)
                    const params = new URLSearchParams(searchParams.toString())
                    if (nextHealth) params.set("healthStatus", nextHealth)
                    else params.delete("healthStatus")
                    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
                  }}
                  className={`w-full px-3 py-2 text-sm border ${dm ? "border-white/[0.08] bg-[#080c15] text-gray-300" : "border-gray-300 bg-white text-gray-700"} rounded-full focus:outline-none focus:ring-1 focus:ring-[#78a530]`}
                >
                  <option value="">Filter Health...</option>
                  <option value="on_track">On Track</option>
                  <option value="at_risk">At Risk</option>
                  <option value="critical">Critical</option>
                </select>
              </div>
              <div className="xl:col-span-1">
                <button
                  type="button"
                  onClick={clearAllFilters}
                  className={`w-full text-sm px-3 py-2 rounded-full border font-medium transition ${
                    appliedFilterLabels.length > 0
                      ? "border-[#78a530] text-[#78a530] bg-[#78a530]/10 hover:bg-[#78a530]/20"
                      : dm
                        ? "border-white/[0.08] text-gray-400 hover:border-[#78a530] hover:text-[#78a530]"
                        : "border-gray-300 text-gray-600 hover:border-[#78a530] hover:text-[#78a530]"
                  }`}
                >
                  {appliedFilterLabels.length > 0 ? `Clear (${appliedFilterLabels.length})` : "Clear"}
                </button>
              </div>
            </div>
          </div>
        </div>

        <table className="w-full table-fixed">
          <colgroup>
            <col className="w-[14%]" />
            <col className="w-[30%]" />
            <col className="w-[12%]" />
            <col className="w-[14%]" />
            <col className="w-[13%]" />
            <col className="w-[13%]" />
            <col className="w-[4%]" />
          </colgroup>
          <thead className={`border-b ${dm ? "border-white/[0.10]" : "border-[#78a530]/20"}`} style={dm ? { background: 'linear-gradient(90deg, #060b16 0%, #0a1020 100%)' } : { background: 'linear-gradient(90deg, #f0f9e0 0%, #f7fbee 100%)' }}>
            <tr>
              <th className={`px-4 py-3 text-left text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"} uppercase tracking-wide`}>
                <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={toggleExpandCollapseAll}
                      className={`inline-flex items-center justify-center w-5 h-5 rounded border ${dm ? "border-white/[0.15] text-gray-300" : "border-gray-300 text-gray-500"} hover:text-[#78a530] hover:border-[#78a530] transition`}
                      title={isExpandAllActive ? "Collapse all" : "Expand all"}
                      aria-label={isExpandAllActive ? "Collapse all rows" : "Expand all rows"}
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      {isExpandAllActive ? (
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 15l-7-7-7 7" />
                      ) : (
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 9l7 7 7-7" />
                      )}
                    </svg>
                  </button>
                  <span>Work Item</span>
                </div>
              </th>
              <th className={`px-4 py-3 text-left text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"} uppercase tracking-wide`}>Name</th>
              <th className={`px-4 py-3 text-left text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"} uppercase tracking-wide`}>Health</th>
              <th className={`px-4 py-3 text-left text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"} uppercase tracking-wide`}>Assignee</th>
              <th className={`px-4 py-3 text-left text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"} uppercase tracking-wide`}>Status</th>
              <th className={`px-4 py-3 text-left text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"} uppercase tracking-wide`}>Due Date</th>
              <th className={`px-4 py-3 text-center text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"} uppercase tracking-wide`}>Tip</th>
            </tr>
          </thead>
          <tbody>
            {hierarchy.length === 0 ? (
              <tr>
                <td colSpan={7} className={`px-4 py-8 text-center ${dm ? "text-gray-400" : "text-gray-500"}`}>
                  No tasks found
                </td>
              </tr>
            ) : (
              hierarchy.map((epic) => renderEpicRow(epic))
            )}
          </tbody>
        </table>
      </div>

      <div className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>
        Showing {healthFilteredTasks.length} of {allTasks.length} tasks
      </div>
    </div>
  )
}
