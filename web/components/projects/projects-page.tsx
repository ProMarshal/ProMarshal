"use client"

import { useState, useEffect, useMemo, useCallback, useRef } from "react"
import { motion } from "framer-motion"
import { CheckCircle2, AlertTriangle, XCircle, Trash2 } from "lucide-react"
import Image from "next/image"
import { signOut, useSession } from "next-auth/react"
import { useRouter, useSearchParams } from "next/navigation"
import { Project } from "@/lib/api"
import { PlannerContent } from "@/components/planner/planner-content"
import { SlackChannelModal } from "@/components/projects/slack-channel-modal"
import { JiraProjectSelectionModal } from "@/components/projects/jira-project-selection-modal"
import { ConfirmationModal } from "@/components/projects/confirmation-modal"
import { FloatingTabMenu, FloatingTabButton } from "@/components/ui/floating-tab-menu"
import { ProjectFeedWidget, PMAlertItem } from "@/components/projects/project-feed-widget"
import { BurndownChart } from "@/components/projects/burndown-chart"
import { VelocityChart } from "@/components/projects/velocity-chart"
import CadenceSummary from "@/components/projects/cadence-summary"
import TeamActivityHeatmap from "@/components/projects/team-activity-heatmap"
import ForecastMindMap from "@/components/projects/forecast-mind-map"
import ProjectHealthHierarchy from "@/components/projects/project-health-hierarchy"
import { Tooltip } from "@/components/ui/tooltip"
import { TimezoneDropdown } from "@/components/ui/timezone-dropdown"
import { renderIssueTypeIcon } from "@/components/projects/work-item-icons"

interface ProjectsPageProps {
  userName: string
  userId: string
  backendToken?: string
  currentProject: Project | null
  projects: Project[]
  initialNav?: string
  initialTab?: string
  backendUrl: string
}

function formatTimezoneLabel(tz: string): string {
  return String(tz || "").replace(/_/g, " ")
}

const SCHEDULE_MINUTE_OPTIONS = ["00", "15", "30", "45"] as const
const SCHEDULE_HOUR_OPTIONS = Array.from({ length: 24 }, (_, index) =>
  String(index).padStart(2, "0")
)

function normalizeQuarterHourTime(value: string, fallback = "09:00"): string {
  const normalized = String(value || "").trim()
  const match = normalized.match(/^(\d{1,2}):(\d{2})$/)
  if (!match) return fallback
  const hour = Math.max(0, Math.min(23, Number(match[1] || 0)))
  const minute = Math.max(0, Math.min(59, Number(match[2] || 0)))
  const closestMinute = SCHEDULE_MINUTE_OPTIONS.reduce((best, candidate) => {
    const candidateValue = Number(candidate)
    const bestValue = Number(best)
    return Math.abs(candidateValue - minute) < Math.abs(bestValue - minute) ? candidate : best
  }, "00" as (typeof SCHEDULE_MINUTE_OPTIONS)[number])
  return `${String(hour).padStart(2, "0")}:${closestMinute}`
}

function splitScheduleTime(value: string, fallback = "09:00"): { hour: string; minute: string } {
  const normalized = normalizeQuarterHourTime(value, fallback)
  const [hour, minute] = normalized.split(":")
  return { hour: hour || "00", minute: minute || "00" }
}

function composeScheduleTime(hour: string, minute: string): string {
  const normalizedHour = SCHEDULE_HOUR_OPTIONS.includes(hour) ? hour : "00"
  const normalizedMinute = SCHEDULE_MINUTE_OPTIONS.includes(minute as (typeof SCHEDULE_MINUTE_OPTIONS)[number])
    ? minute
    : "00"
  return `${normalizedHour}:${normalizedMinute}`
}

function normalizePulseTab(tab: string | null | undefined): string {
  const normalized = String(tab || "").trim().toLowerCase()
  if (normalized === "project health" || normalized === "work item") return "Work Item"
  if (normalized === "action items" || normalized === "action item" || normalized === "actionitems" || normalized === "actionitem") return "Action Items"
  return String(tab || "").trim()
}

function hashText(value: string): number {
  let hash = 0
  const text = String(value || "")
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) - hash) + text.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash)
}

const ACTION_ITEM_NOTE_COLOR_CHOICES = [
  "#F4A79D",
  "#F5BF66",
  "#F2D15E",
  "#E9E292",
  "#7ED59D",
  "#CFCFCF",
  "#E8A8DF",
  "#C5A6E8",
  "#75B9E8",
  "#6EC8DF",
]

// ── Meeting Insights helpers ───────────────────────────────────────────────

function formatMeetingDate(isoString: string | null | undefined): string {
  if (!isoString) return "Unknown date"
  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return "Unknown date"
  const now = new Date()
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diffDays = Math.round((startOfDay(now).getTime() - startOfDay(date).getTime()) / 86400000)
  if (diffDays === 0) return "Today"
  if (diffDays === 1) return "Yesterday"
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: date.getFullYear() === now.getFullYear() ? undefined : "numeric" })
}

const DISCUSSED_POINT_STYLES: Record<string, { label: string; dark: string; light: string }> = {
  action: { label: "ACTION", dark: "bg-[#78a530]/15 text-[#a8d060] border-[#78a530]/30", light: "bg-[#f3f9e8] text-[#5f8724] border-[#c4de87]" },
  decision: { label: "DECISION", dark: "bg-[#3a5a8c]/20 text-[#9bbce8] border-[#3a5a8c]/40", light: "bg-[#eef3fb] text-[#3a5a8c] border-[#a8c4ea]" },
  blocker: { label: "BLOCKER", dark: "bg-[#e58e26]/15 text-[#f2b56e] border-[#e58e26]/30", light: "bg-[#fdf3e6] text-[#b8721a] border-[#f0c890]" },
  discussion: { label: "DISCUSSION", dark: "bg-white/[0.06] text-gray-300 border-white/[0.12]", light: "bg-gray-100 text-gray-600 border-gray-200" },
  question: { label: "QUESTION", dark: "bg-[#7b5fb5]/15 text-[#b9a3e0] border-[#7b5fb5]/30", light: "bg-[#f1ebfa] text-[#5f4795] border-[#cdb7eb]" },
}

function DiscussedPointCard({ point, dm, textPrimary, textSecondary }: {
  point: { type: string; text: string; owner?: string; due?: string }
  dm: boolean
  textPrimary: string
  textSecondary: string
}) {
  const styleSet = DISCUSSED_POINT_STYLES[point.type] || DISCUSSED_POINT_STYLES.discussion
  const tagClass = dm ? styleSet.dark : styleSet.light
  const isAction = point.type === "action"
  return (
    <div className={`rounded-lg p-3 border ${dm ? "border-white/[0.08]" : "border-gray-200"} ${dm ? "bg-white/[0.02]" : "bg-white"}`}>
      <div className="flex items-start gap-2">
        <span className={`inline-flex items-center text-[10px] font-bold tracking-widest px-2 py-0.5 rounded border ${tagClass}`}>
          {styleSet.label}
        </span>
      </div>
      <p className={`text-sm ${textPrimary} mt-2`}>
        {isAction && point.owner ? <span className="font-semibold">{point.owner} — </span> : null}
        {point.text}
      </p>
      {isAction && (
        <p className={`text-xs ${textSecondary} mt-1`}>
          {point.due ? `Due: ${point.due}` : "No deadline mentioned"}
        </p>
      )}
    </div>
  )
}

// ── Pulse helper components ────────────────────────────────────────────────

function PulseSection({ id, title, accentColor, highlight, count, emptyText, emptyIcon, description, children, isDarkMode, surfaceStyle, headerStyle }: {
  id: string; title: string; accentColor: string; highlight: boolean
  count: number; emptyText: string; emptyIcon: string; description?: string; children?: React.ReactNode
  isDarkMode: boolean
  surfaceStyle?: React.CSSProperties
  headerStyle?: React.CSSProperties
}) {
  const borderColor = highlight ? accentColor : (isDarkMode ? "rgba(255,255,255,0.12)" : "#d1d5db")

  return (
    <div
      id={`pulse-${id}`}
      className={`rounded-2xl border shadow-sm overflow-hidden transition-all ${isDarkMode ? "bg-[#080d1a]" : "bg-white"} ${highlight ? "ring-2 ring-offset-2" : ""}`}
      style={{
        borderColor,
        ...(highlight ? { "--tw-ring-color": accentColor } as React.CSSProperties : {}),
        ...(surfaceStyle || {}),
      }}
    >
      <div
        className={`px-5 py-3.5 border-b flex items-center gap-3 ${isDarkMode ? "border-white/[0.06]" : "border-gray-100"}`}
        style={{
          ...(headerStyle || {}),
          background: isDarkMode
            ? `linear-gradient(90deg, rgba(6,11,22,0.95) 0%, rgba(10,16,32,0.95) 100%), linear-gradient(to right, ${accentColor}18, rgba(8,13,26,0) 70%)`
            : `linear-gradient(to right, ${accentColor}10, white 70%)`,
        }}
      >
        <div className="w-1.5 h-5 rounded-full flex-shrink-0" style={{ backgroundColor: accentColor }} />
        <div className="flex-1">
          <h3 className={`text-sm font-semibold ${isDarkMode ? "text-gray-100" : "text-gray-900"}`}>{title}</h3>
          {description && <p className={`text-xs mt-0.5 ${isDarkMode ? "text-gray-400" : "text-gray-500"}`}>{description}</p>}
        </div>
        {count > 0 && (
          <span className="text-xs font-semibold px-2 py-0.5 rounded-full text-white" style={{ backgroundColor: accentColor }}>
            {count}
          </span>
        )}
      </div>
      {count === 0 ? (
        <p className={`text-sm text-center py-8 ${isDarkMode ? "text-gray-400" : "text-gray-500"}`}>{emptyIcon} {emptyText}</p>
      ) : (
        <div className={`divide-y ${isDarkMode ? "divide-white/[0.06]" : "divide-gray-100"}`}>{children}</div>
      )}
    </div>
  )
}

function PulseTaskRow({ task, badge, isDarkMode }: { task: any; badge: { badge: string; label: string }; isDarkMode: boolean }) {
  return (
    <div className={`flex items-center justify-between px-5 py-3 transition rounded-lg mx-1 ${isDarkMode ? "hover:bg-white/[0.05]" : "hover:bg-gray-50/80"}`}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={`text-xs font-mono flex-shrink-0 ${isDarkMode ? "text-gray-500" : "text-gray-400"}`}>{task.task_key}</span>
          <p className={`text-sm font-medium truncate ${isDarkMode ? "text-gray-100" : "text-gray-900"}`}>{task.title}</p>
        </div>
        {task.assignee_name && (
          <p className={`text-xs mt-0.5 ${isDarkMode ? "text-gray-400" : "text-gray-500"}`}>{task.assignee_name}</p>
        )}
      </div>
      <div className="flex items-center gap-2 ml-3 flex-shrink-0">
        <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${badge.badge}`}>{badge.label}</span>
        {task.url && (
          <a href={task.url} target="_blank" rel="noopener noreferrer" className="text-xs text-[#78a530] font-semibold hover:underline">View →</a>
        )}
      </div>
    </div>
  )
}

function CompactNumberDropdown({
  value,
  options,
  onChange,
  dm,
  ariaLabel,
}: {
  value: string
  options: readonly string[] | string[]
  onChange: (next: string) => void
  dm: boolean
  ariaLabel: string
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current) return
      if (!rootRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false)
    }
    document.addEventListener("mousedown", onPointerDown)
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("mousedown", onPointerDown)
      document.removeEventListener("keydown", onKeyDown)
    }
  }, [open])

  const buttonClass = `w-20 px-2 py-2 border ${dm ? "border-white/10 bg-[#111520] text-gray-100" : "border-gray-300 bg-white text-gray-900"} rounded-lg focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent`
  const menuClass = `${dm ? "border-white/10 bg-[#0b1328]" : "border-gray-300 bg-white"} absolute left-0 top-full z-50 mt-1 w-20 max-h-44 overflow-y-auto rounded-lg border shadow-lg`

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        className={`${buttonClass} flex items-center justify-between text-base`}
      >
        <span>{value}</span>
        <svg className="h-4 w-4 opacity-80" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 0 1 1.06.02L10 11.17l3.71-3.94a.75.75 0 1 1 1.08 1.04l-4.25 4.5a.75.75 0 0 1-1.08 0l-4.25-4.5a.75.75 0 0 1 .02-1.06Z" clipRule="evenodd" />
        </svg>
      </button>
      {open && (
        <div role="listbox" aria-label={ariaLabel} className={menuClass}>
          {options.map((option) => {
            const active = option === value
            return (
              <button
                key={`${ariaLabel}-${option}`}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => {
                  onChange(option)
                  setOpen(false)
                }}
                className={`block w-full px-2 py-1.5 text-left text-base leading-none ${active ? "bg-[#2563eb] text-white" : dm ? "text-gray-100 hover:bg-white/10" : "text-gray-900 hover:bg-gray-100"}`}
              >
                {option}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function normalizeBackendRecommendations(input: any): PMAlertItem[] {
  const recommendations = Array.isArray(input) ? input : []
  return recommendations
    .map((rec: any) => ({
      id: String(rec?.id || ""),
      taskKey: rec?.taskKey ? String(rec.taskKey) : null,
      title: String(rec?.title || rec?.taskKey || ""),
      reason: String(rec?.reason || ""),
      recommendation: String(rec?.recommendation || ""),
      severity: rec?.severity === "critical" || rec?.severity === "at_risk" || rec?.severity === "info" ? rec.severity : "info",
      bucket: rec?.bucket === "active" || rec?.bucket === "up_next" ? rec.bucket : "active",
      category: rec?.category === "best_practice" || rec?.category === "work_item" ? rec.category : "work_item",
      confidence: typeof rec?.confidence === "number" ? rec.confidence : undefined,
      why: Array.isArray(rec?.why) ? rec.why.filter((item: any) => typeof item === "string").map((item: string) => item.trim()).filter(Boolean) : undefined,
      assignee_suggestion: rec?.assignee_suggestion ? String(rec.assignee_suggestion) : null,
    }))
    .filter((rec: PMAlertItem) => !!rec.id && !!rec.title && !!rec.recommendation)
}

// ────────────────────────────────────────────────────────────────────────────

export function ProjectsPage({ userName, userId, backendToken, currentProject, projects, initialNav, initialTab, backendUrl }: ProjectsPageProps) {
  const router = useRouter()
  const { data: session } = useSession()
  const searchParams = useSearchParams()
  const navFromUrl = searchParams.get("nav")
  const tabFromUrl = searchParams.get("tab")
  const runtimeBackendUrl = useMemo(
    () => process.env.NEXT_PUBLIC_PYTHON_API_URL || backendUrl,
    [backendUrl]
  )
  const effectiveBackendToken = useMemo(
    () => String(backendToken || session?.user?.backendToken || "").trim(),
    [backendToken, session?.user?.backendToken]
  )
  const backendFetch = useCallback((input: RequestInfo | URL, init: RequestInit = {}) => {
    const headers = new Headers(init.headers || {})
    if (effectiveBackendToken && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${effectiveBackendToken}`)
    }
    return window.fetch(input, { ...init, headers })
  }, [effectiveBackendToken])

  // Helper function to get integration data from canonical structure
  const getIntegration = (project: Project | null, tool: string) => {
    if (!project) return null
    return (project as any).integrations?.[tool] || null
  }

  const hasConnectedCategory = useCallback(
    (project: Project | null, category: string) => {
      if (!project) return false
      const integrations = (project as any)?.integrations
      if (!integrations || typeof integrations !== "object") return false

      const targetCategory = String(category || "").trim().toLowerCase()
      if (!targetCategory) return false

      for (const config of Object.values(integrations)) {
        const normalizedStatus = String((config as any)?.status || "").trim().toLowerCase()
        const normalizedCategory = String((config as any)?.category || "").trim().toLowerCase()
        if (normalizedStatus === "connected" && normalizedCategory === targetCategory) {
          return true
        }
      }

      return false
    },
    []
  )

  // Sort projects by creation date (latest first)
  const sortedProjects = [...projects].sort((a, b) => {
    const dateA = new Date(a.created_at || 0).getTime()
    const dateB = new Date(b.created_at || 0).getTime()
    return dateB - dateA // Most recent first
  })

  const [selectedProject, setSelectedProject] = useState<Project | null>(
    currentProject || sortedProjects[0]
  )
  const isSelectedProjectOwner = useMemo(() => {
    const currentUserId = String(userId || "").trim()
    const creatorMatch = String((selectedProject as any)?.user_id || "").trim() === currentUserId
    const memberOwnerMatch = ((selectedProject as any)?.members || []).some(
      (m: any) => String(m.user_id || m.email || "").trim() === currentUserId && m.role === "owner"
    )
    return creatorMatch || memberOwnerMatch
  }, [selectedProject, userId])

  const [isDarkMode, setIsDarkMode] = useState(true)
  const [themeReady, setThemeReady] = useState(false)
  const [showUserMenu, setShowUserMenu] = useState(false)
  const [showProjectDropdown, setShowProjectDropdown] = useState(false)
  const isHiddenStaticNav = useCallback(
    (navValue: string | null | undefined) => {
      const normalized = String(navValue || "").trim()
      return normalized === "Project Charter" || normalized === "Forecast" || normalized === "Reports"
    },
    []
  )
  const [activeNav, setActiveNav] = useState(() => {
    const nav = navFromUrl || initialNav || "PM Board"
    return isHiddenStaticNav(nav) ? "PM Board" : nav
  })
  const [activeControlPanelTab, setActiveControlPanelTab] = useState(() => {
    if (navFromUrl === "Control Panel" && tabFromUrl) return tabFromUrl
    if (initialNav === "Control Panel" && initialTab) return initialTab
    return "Manage Team"
  })
  const [activeForecastTab, setActiveForecastTab] = useState("Risks")
  const [activeRiskTab, setActiveRiskTab] = useState("Active")
  const [activeBlockerTab, setActiveBlockerTab] = useState("Active")
  const [activeReportsTab, setActiveReportsTab] = useState("Weekly Summary")
  const [activePulseTab, setActivePulseTab] = useState(() => {
    if (navFromUrl === "Pulse" && tabFromUrl) {
      return normalizePulseTab(tabFromUrl)
    }
    if (initialNav === "Pulse" && initialTab) {
      return normalizePulseTab(initialTab)
    }
    return "Action Items"
  })
  useEffect(() => {
    if (!isHiddenStaticNav(activeNav)) return
    setActiveNav("PM Board")
  }, [activeNav, isHiddenStaticNav])
  const [pendingPulseHealthStatus, setPendingPulseHealthStatus] = useState<"on_track" | "at_risk" | "critical" | null>(null)
  const [pulseRecCounts, setPulseRecCounts] = useState<{ workItem: number; bestPractice: number }>({ workItem: 0, bestPractice: 0 })
  const [pulseRecPanelOpen, setPulseRecPanelOpen] = useState(false)
  const [activeAnalyticsView, setActiveAnalyticsView] = useState<"weekly" | "monthly">("weekly")

  // Active Sprint Info
  const [activeSprint, setActiveSprint] = useState<{
    name: string
    startDate: string
    endDate: string
  } | null>(null)
  const [showSprintNotification, setShowSprintNotification] = useState(false)

  // Project Health (Adaptive: Epic/Sprint/Task based)
  const [projectHealth, setProjectHealth] = useState<{
    healthType: string
    onTrack: number
    atRisk: number
    critical: number
    total: number
    displayLabel: string
  }>({ healthType: 'task', onTrack: 0, atRisk: 0, critical: 0, total: 0, displayLabel: 'Tasks' })

  // Project Health Hierarchy Data (for collapsible view)
  const [healthHierarchy, setHealthHierarchy] = useState<any>(null)
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set())
  const [showFlatCompleted, setShowFlatCompleted] = useState(false)
  const [collapsedFlatSections, setCollapsedFlatSections] = useState<Record<string, boolean>>({})
  const [activeHealthFilter, setActiveHealthFilter] = useState<"on_track" | "at_risk" | "critical" | null>(null)
  const [animatedHealth, setAnimatedHealth] = useState({ completed: 0, total: 0, onTrack: 0, atRisk: 0, critical: 0 })
  const [hoveredFocusTask, setHoveredFocusTask] = useState<string | null>(null)
  const [hoveredInboxItem, setHoveredInboxItem] = useState<string | null>(null)

  // Pulse data
  const [tasksDue24h, setTasksDue24h] = useState<any[]>([])
  const [tomorrowAvailability, setTomorrowAvailability] = useState<any[]>([])
  const [unassignedUrgent, setUnassignedUrgent] = useState<any[]>([])
  const [silentTasks, setSilentTasks] = useState<any[]>([])
  const [staleReviews, setStaleReviews] = useState<any[]>([])
  const [actionItemOpenCount, setActionItemOpenCount] = useState(0)
  const [pulseActionItems, setPulseActionItems] = useState<any[]>([])
  const [pulseActionItemsLoading, setPulseActionItemsLoading] = useState(false)
  const [pulseActionItemsError, setPulseActionItemsError] = useState<string | null>(null)
  const [pulseActionItemView, setPulseActionItemView] = useState<string>("open")
  const [pulseActionItemsRefreshNonce, setPulseActionItemsRefreshNonce] = useState(0)
  // Meeting Insights state
  const [meetings, setMeetings] = useState<any[]>([])
  const [meetingsLoading, setMeetingsLoading] = useState(false)
  const [meetingsError, setMeetingsError] = useState<string | null>(null)
  const [selectedMeetingId, setSelectedMeetingId] = useState<string | null>(null)
  const [meetingDetail, setMeetingDetail] = useState<any>(null)
  const [meetingDetailLoading, setMeetingDetailLoading] = useState(false)
  const [pulseWorkItemRefreshSignal, setPulseWorkItemRefreshSignal] = useState(0)
  const [pulseWorkItemRefreshing, setPulseWorkItemRefreshing] = useState(false)
  const [actionItemMutationPending, setActionItemMutationPending] = useState<Record<string, boolean>>({})
  const [showAddActionItemModal, setShowAddActionItemModal] = useState(false)
  const [addActionItemTitle, setAddActionItemTitle] = useState("")
  const [addActionItemOwnerUserId, setAddActionItemOwnerUserId] = useState("")
  const [addActionItemDueDate, setAddActionItemDueDate] = useState("")
  const [addActionItemColor, setAddActionItemColor] = useState<string>(ACTION_ITEM_NOTE_COLOR_CHOICES[0])
  const [activeActionItemColorPickerId, setActiveActionItemColorPickerId] = useState<string | null>(null)
  const [addActionItemSubmitting, setAddActionItemSubmitting] = useState(false)
  const [addActionItemError, setAddActionItemError] = useState<string | null>(null)
  const [pulseLoading, setPulseLoading] = useState(false)
  const [showInviteModal, setShowInviteModal] = useState(false)
  const [showReviewModal, setShowReviewModal] = useState(false)
  const [showSlackChannelModal, setShowSlackChannelModal] = useState(false)
  const [slackSetupProjectId, setSlackSetupProjectId] = useState<string | null>(null)
  const [pendingSlackAutoOpenProjectId, setPendingSlackAutoOpenProjectId] = useState<string | null>(null)
  const [showJiraProjectSelectionModal, setShowJiraProjectSelectionModal] = useState(false)
  const [jiraSelectionProjectId, setJiraSelectionProjectId] = useState<string | null>(null)
  const [isLoadingJiraProjectSelection, setIsLoadingJiraProjectSelection] = useState(false)
  const [inviteEmail, setInviteEmail] = useState("")
  const [inviteJiraAccountId, setInviteJiraAccountId] = useState<string | null>(null)
  const [inviteError, setInviteError] = useState("")
  const [isInviting, setIsInviting] = useState(false)
  const [teamMembers, setTeamMembers] = useState<any[]>([])
  const [editingMemberRoleUserId, setEditingMemberRoleUserId] = useState<string | null>(null)
  const [editingMemberRoleValue, setEditingMemberRoleValue] = useState<"owner" | "member">("member")
  const [updatingMemberRoleUserId, setUpdatingMemberRoleUserId] = useState<string | null>(null)
  const [memberRoleError, setMemberRoleError] = useState<string | null>(null)
  const [memberToDelete, setMemberToDelete] = useState<any | null>(null)
  const [isRemovingMember, setIsRemovingMember] = useState(false)
  const [removeMemberError, setRemoveMemberError] = useState<string | null>(null)
  const [orphanAssignees, setOrphanAssignees] = useState<any[]>([])
  const [pendingInvites, setPendingInvites] = useState<any[]>([])
  const [backendRecommendations, setBackendRecommendations] = useState<PMAlertItem[]>([])
  const [remindSuccess, setRemindSuccess] = useState<string | null>(null)
  const isToolsConnected = useCallback((project: Project | null) => {
    return hasConnectedCategory(project, "communication") && hasConnectedCategory(project, "workitem")
  }, [hasConnectedCategory])

  const [isQuickActionsExpanded, setIsQuickActionsExpanded] = useState(
    () => !isToolsConnected(currentProject || sortedProjects[0])
  )
  const [charterStatus, setCharterStatus] = useState<any>(null)

  // Project Settings form state
  const [settingsTimezone, setSettingsTimezone] = useState("")
  const [settingsReminderEnabled, setSettingsReminderEnabled] = useState(true)
  const [settingsReminderTime, setSettingsReminderTime] = useState("09:00")
  const [settingsReminderSkipWeekends, setSettingsReminderSkipWeekends] = useState(false)
  const [settingsReminderIgnoreFollowupForOwner, setSettingsReminderIgnoreFollowupForOwner] = useState(false)
  const [savedReminderEnabled, setSavedReminderEnabled] = useState(true)
  const [savedReminderTime, setSavedReminderTime] = useState("09:00")
  const [savedReminderSkipWeekends, setSavedReminderSkipWeekends] = useState(false)
  const [savedReminderIgnoreFollowupForOwner, setSavedReminderIgnoreFollowupForOwner] = useState(false)
  const [settingsActionItemDigestEnabled, setSettingsActionItemDigestEnabled] = useState(true)
  const [settingsActionItemDigestTime, setSettingsActionItemDigestTime] = useState("12:30")
  const [settingsActionItemDigestSkipWeekends, setSettingsActionItemDigestSkipWeekends] = useState(false)
  const [settingsActionItemDigestIgnoreFollowupForOwner, setSettingsActionItemDigestIgnoreFollowupForOwner] = useState(false)
  const [savedActionItemDigestEnabled, setSavedActionItemDigestEnabled] = useState(true)
  const [savedActionItemDigestTime, setSavedActionItemDigestTime] = useState("12:30")
  const [savedActionItemDigestSkipWeekends, setSavedActionItemDigestSkipWeekends] = useState(false)
  const [savedActionItemDigestIgnoreFollowupForOwner, setSavedActionItemDigestIgnoreFollowupForOwner] = useState(false)
  const [hasUnsavedSettings, setHasUnsavedSettings] = useState(false)
  const [isSavingSettings, setIsSavingSettings] = useState(false)
  const [settingsSaveSuccess, setSettingsSaveSuccess] = useState(false)
  const [isDisconnectingSlack, setIsDisconnectingSlack] = useState(false)
  const [isDisconnectingJira, setIsDisconnectingJira] = useState(false)
  const [showJiraDisconnectConfirmModal, setShowJiraDisconnectConfirmModal] = useState(false)
  const [isSubmittingJiraDisconnectChoice, setIsSubmittingJiraDisconnectChoice] = useState(false)
  const [showJiraStaleDataWarningModal, setShowJiraStaleDataWarningModal] = useState(false)
  const [jiraStaleWorkItemCount, setJiraStaleWorkItemCount] = useState(0)
  const [isClearingJiraStaleData, setIsClearingJiraStaleData] = useState(false)
  const [isConnectingSlack, setIsConnectingSlack] = useState(false)
  const [isConnectingJira, setIsConnectingJira] = useState(false)
  const [isSyncingSlack, setIsSyncingSlack] = useState(false)
  const [slackSyncSuccess, setSlackSyncSuccess] = useState(false)
  const [isSyncingJira, setIsSyncingJira] = useState(false)
  const [jiraSyncSuccess, setJiraSyncSuccess] = useState(false)
  const [jiraSyncError, setJiraSyncError] = useState<string | null>(null)
  const [availableTimezones, setAvailableTimezones] = useState<string[]>([])
  const [showDeleteProjectModal, setShowDeleteProjectModal] = useState(false)
  const [deleteProjectConfirmText, setDeleteProjectConfirmText] = useState("")
  const [isDeletingProject, setIsDeletingProject] = useState(false)
  const [deleteProjectError, setDeleteProjectError] = useState<string | null>(null)
  const [deleteProjectSuccessBanner, setDeleteProjectSuccessBanner] = useState<string | null>(null)
  const deleteRedirectTimeoutRef = useRef<number | null>(null)

  // Analytics state
  const [overdueTasks, setOverdueTasks] = useState<any[]>([])
  const [tasksToday, setTasksToday] = useState<any[]>([])
  const [tasksTomorrow, setTasksTomorrow] = useState<any[]>([])
  const [bottlenecks, setBottlenecks] = useState<any[]>([])
  const [burndownData, setBurndownData] = useState<any>(null)
  const [velocityData, setVelocityData] = useState<any>(null)
  const [pmBoardRefreshNonce, setPmBoardRefreshNonce] = useState(0)
  const [pmBoardRefreshing, setPmBoardRefreshing] = useState(false)
  const [pmBoardSummaryLoaded, setPmBoardSummaryLoaded] = useState(false)
  const [pmBoardSummaryError, setPmBoardSummaryError] = useState<string | null>(null)
  const [pmBoardSummaryProcessing, setPmBoardSummaryProcessing] = useState(false)
  const [pmBoardRefreshTrigger, setPmBoardRefreshTrigger] = useState<"initial_load" | "project_switch" | "manual_refresh" | "poll" | "retry" | "status_ready">("initial_load")
  const pulseHealthNavIntentRef = useRef(false)

  const triggerPmBoardRefresh = useCallback((
    isManual = false,
    trigger: "initial_load" | "project_switch" | "manual_refresh" | "poll" | "retry" | "status_ready" = "manual_refresh"
  ) => {
    if (!selectedProject || activeNav !== "PM Board") return
    setPmBoardRefreshTrigger(trigger)
    if (isManual) setPmBoardRefreshing(true)
    setPmBoardRefreshNonce((prev) => prev + 1)
  }, [selectedProject, activeNav])

  const markPmBoardUpdated = useCallback(() => {
    if (activeNav !== "PM Board") return
    setPmBoardRefreshing(false)
  }, [activeNav])

  const openPulseProjectHealthFocus = useCallback((params: {
    focusType: "epic" | "story" | "task"
    focusKey: string
    focusSeverity?: string
    focusParentKey?: string
  }) => {
    if (!selectedProject?._id) return

    // Navigation intent guard: prevent stale PM Board state from rewriting URL in transition.
    pulseHealthNavIntentRef.current = true

    // Drive navigation via URL first; local state will follow from search params sync.
    const nextParams = new URLSearchParams(window.location.search)
    nextParams.set("nav", "Pulse")
    nextParams.set("tab", "Work Item")
    nextParams.set("projectId", selectedProject._id)
    nextParams.set("focusType", params.focusType)
    nextParams.set("focusKey", params.focusKey)
    nextParams.set("from", "pm-board")
    if (params.focusSeverity) nextParams.set("focusSeverity", params.focusSeverity)
    else nextParams.delete("focusSeverity")
    if (params.focusParentKey) nextParams.set("focusParentKey", params.focusParentKey)
    else nextParams.delete("focusParentKey")
    nextParams.delete("quickFocus")

    router.replace(`/projects?${nextParams.toString()}`, { scroll: false })
  }, [selectedProject?._id, router])

  const openPulseProjectHealthByStatus = useCallback((status: "on_track" | "at_risk" | "critical") => {
    if (!selectedProject?._id) return

    // Navigation intent guard: prevent stale PM Board state from rewriting URL in transition.
    pulseHealthNavIntentRef.current = true
    setPendingPulseHealthStatus(status)

    // Drive navigation via URL first; local state will follow from search params sync.
    const nextParams = new URLSearchParams(window.location.search)
    nextParams.set("nav", "Pulse")
    nextParams.set("tab", "Work Item")
    nextParams.set("projectId", selectedProject._id)
    nextParams.set("healthStatus", status)
    nextParams.delete("focusType")
    nextParams.delete("focusKey")
    nextParams.delete("focusParentKey")
    nextParams.delete("focusSeverity")
    nextParams.delete("from")
    nextParams.delete("quickFocus")

    router.replace(`/projects?${nextParams.toString()}`, { scroll: false })
  }, [selectedProject?._id, router])

  const openPulseWorkItemRecommendations = useCallback(() => {
    if (!selectedProject?._id) return
    pulseHealthNavIntentRef.current = true
    const nextParams = new URLSearchParams(window.location.search)
    nextParams.set("nav", "Pulse")
    nextParams.set("tab", "Work Item")
    nextParams.set("projectId", selectedProject._id)
    nextParams.set("workItemView", "recommendations")
    nextParams.set("recommendationCategory", "work_item")
    nextParams.delete("healthStatus")
    nextParams.delete("quickFocus")
    router.replace(`/projects?${nextParams.toString()}`, { scroll: false })
  }, [selectedProject?._id, router])

  const openPulseBestPracticeRecommendations = useCallback(() => {
    if (!selectedProject?._id) return
    pulseHealthNavIntentRef.current = true
    const nextParams = new URLSearchParams(window.location.search)
    nextParams.set("nav", "Pulse")
    nextParams.set("tab", "Work Item")
    nextParams.set("projectId", selectedProject._id)
    nextParams.set("workItemView", "recommendations")
    nextParams.set("recommendationCategory", "best_practice")
    nextParams.delete("healthStatus")
    nextParams.delete("quickFocus")
    router.replace(`/projects?${nextParams.toString()}`, { scroll: false })
  }, [selectedProject?._id, router])

  const openPulseTodayFocus = useCallback(() => {
    if (!selectedProject?._id) return
    pulseHealthNavIntentRef.current = true
    const nextParams = new URLSearchParams(window.location.search)
    nextParams.set("nav", "Pulse")
    nextParams.set("tab", "Work Item")
    nextParams.set("projectId", selectedProject._id)
    nextParams.set("quickFocus", "today_focus")
    nextParams.delete("workItemView")
    nextParams.delete("healthStatus")
    nextParams.delete("focusType")
    nextParams.delete("focusKey")
    nextParams.delete("focusParentKey")
    nextParams.delete("focusSeverity")
    nextParams.delete("from")
    router.replace(`/projects?${nextParams.toString()}`, { scroll: false })
  }, [selectedProject?._id, router])

  const openPulseActionItems = useCallback(() => {
    if (!selectedProject?._id) return
    pulseHealthNavIntentRef.current = true
    const nextParams = new URLSearchParams(window.location.search)
    nextParams.set("nav", "Pulse")
    nextParams.set("tab", "Action Items")
    nextParams.set("projectId", selectedProject._id)
    nextParams.delete("workItemView")
    nextParams.delete("recommendationCategory")
    nextParams.delete("healthStatus")
    nextParams.delete("quickFocus")
    nextParams.delete("focusType")
    nextParams.delete("focusKey")
    nextParams.delete("focusParentKey")
    nextParams.delete("focusSeverity")
    nextParams.delete("from")
    router.replace(`/projects?${nextParams.toString()}`, { scroll: false })
  }, [selectedProject?._id, router])

  // Restore dark mode preference after mount (avoids SSR hydration mismatch)
  useEffect(() => {
    const saved = localStorage.getItem("promarshal_dark_mode")
    if (saved !== null) setIsDarkMode(saved === "true")
    setThemeReady(true)
  }, [])

  useEffect(() => {
    if (activeNav !== "Pulse" || activePulseTab !== "Work Item") {
      setPendingPulseHealthStatus(null)
    }
  }, [activeNav, activePulseTab])

  const isCompletedStatus = useCallback((status: string | undefined) => {
    const normalized = (status || "").toLowerCase().replace(/\s+/g, "_")
    return ["done", "closed", "resolved", "completed"].includes(normalized)
  }, [])

  const healthUnits = useMemo(() => {
    if (!healthHierarchy) return { units: [], label: projectHealth.displayLabel || "Tasks" }

    const parentItems = healthHierarchy.epics || healthHierarchy.sprints || healthHierarchy.tasks || []
    const leafItems = parentItems.flatMap((item: any) => item.stories || item.tasks || [])
    const baseLeaf = leafItems.length > 0 ? leafItems : parentItems

    const storyItems = baseLeaf.filter((item: any) => (item.issue_type || "").toLowerCase() === "story")
    if (storyItems.length > 0) {
      return { units: storyItems, label: "Stories" }
    }
    return { units: baseLeaf, label: "Tasks" }
  }, [healthHierarchy, projectHealth.displayLabel])

  const healthMetrics = useMemo(() => {
    const openUnits = healthUnits.units.filter((item: any) => !isCompletedStatus(item.status))
    const completedUnits = healthUnits.units.filter((item: any) => isCompletedStatus(item.status))
    const onTrack = openUnits.filter((item: any) => item.health_status === "on_track").length
    const atRisk = openUnits.filter((item: any) => item.health_status === "at_risk").length
    const critical = openUnits.filter((item: any) => item.health_status === "critical").length
    const totalOpen = openUnits.length

    // Overall score includes completed work as healthy progress.
    const totalForScore = healthUnits.units.length
    const weightedHealthy = completedUnits.length + onTrack + (atRisk * 0.5)
    const score = totalForScore === 0 ? 100 : Math.round((weightedHealthy / totalForScore) * 100)

    return { onTrack, atRisk, critical, total: totalOpen, score, label: healthUnits.label }
  }, [healthUnits, isCompletedStatus])

  const pmTaskHealthMetrics = useMemo(() => {
    const rootItems = healthHierarchy?.epics || healthHierarchy?.sprints || healthHierarchy?.tasks || []
    const nestedItems = rootItems.flatMap((item: any) => item.stories || item.tasks || [])
    const taskItems = nestedItems.length > 0 ? nestedItems : rootItems

    const completed = taskItems.filter((item: any) => isCompletedStatus(item.status))
    const open = taskItems.filter((item: any) => !isCompletedStatus(item.status))
    const onTrack = open.filter((item: any) => item.health_status === "on_track").length
    const atRisk = open.filter((item: any) => item.health_status === "at_risk").length
    const critical = open.filter((item: any) => item.health_status === "critical").length

    return {
      onTrack,
      atRisk,
      critical,
      totalOpen: open.length,
      totalItems: taskItems.length,
      completedCount: completed.length,
      items: taskItems,
    }
  }, [healthHierarchy, isCompletedStatus])

  const pmWorkItemCompletion = useMemo(() => {
    const apiSummary = healthHierarchy?.work_item_summary
    if (apiSummary && typeof apiSummary === "object") {
      return {
        total: Number(apiSummary.total || 0),
        completed: Number(apiSummary.completed || 0),
        openTotal: Number(apiSummary.open_total || 0),
        onTrack: Number(apiSummary.on_track || 0),
        atRisk: Number(apiSummary.at_risk || 0),
        critical: Number(apiSummary.critical || 0),
      }
    }

    const roots = healthHierarchy?.epics || healthHierarchy?.sprints || healthHierarchy?.tasks || []
    const allNodes: any[] = []

    const isNodeCompleted = (node: any): boolean => {
      const children = Array.isArray(node?.stories) ? node.stories : Array.isArray(node?.tasks) ? node.tasks : []
      if (children.length > 0) return children.every((child: any) => isNodeCompleted(child))
      return isCompletedStatus(node?.status)
    }

    const walk = (nodes: any[]) => {
      nodes.forEach((node) => {
        allNodes.push(node)
        const children = Array.isArray(node?.stories) ? node.stories : Array.isArray(node?.tasks) ? node.tasks : []
        if (children.length > 0) walk(children)
      })
    }

    walk(roots)

    const completed = allNodes.filter((node) => isNodeCompleted(node)).length
    const openNodes = allNodes.filter((node) => !isNodeCompleted(node))
    const onTrack = openNodes.filter((node) => node?.health_status === "on_track").length
    const atRisk = openNodes.filter((node) => node?.health_status === "at_risk").length
    const critical = openNodes.filter((node) => node?.health_status === "critical").length

    return {
      total: allNodes.length,
      completed,
      openTotal: openNodes.length,
      onTrack,
      atRisk,
      critical,
    }
  }, [healthHierarchy, isCompletedStatus])

  useEffect(() => {
    const target = pmWorkItemCompletion
    if (!target.total && !target.onTrack && !target.atRisk && !target.critical) {
      setAnimatedHealth({ completed: 0, total: 0, onTrack: 0, atRisk: 0, critical: 0 })
      return
    }
    const steps = 60
    const duration = 2400
    let step = 0
    const timer = setInterval(() => {
      step++
      const t = step / steps
      const ease = 1 - Math.pow(1 - t, 3)
      setAnimatedHealth({
        completed: Math.round(target.completed * ease),
        total: target.total,
        onTrack: Math.round(target.onTrack * ease),
        atRisk: Math.round(target.atRisk * ease),
        critical: Math.round(target.critical * ease),
      })
      if (step >= steps) {
        clearInterval(timer)
        setAnimatedHealth(target)
      }
    }, duration / steps)
    return () => clearInterval(timer)
  }, [pmWorkItemCompletion.completed, pmWorkItemCompletion.onTrack, pmWorkItemCompletion.atRisk, pmWorkItemCompletion.critical, pmWorkItemCompletion.total])

  const hasConnectedWorkItemTool = useMemo(
    () => hasConnectedCategory(selectedProject, "workitem"),
    [hasConnectedCategory, selectedProject]
  )

  const shouldShowWorkItemWidgets = hasConnectedWorkItemTool

  const pmFilteredHealthItems = useMemo(() => {
    if (!activeHealthFilter) return []
    return pmTaskHealthMetrics.items.filter(
      (item: any) => !isCompletedStatus(item.status) && item.health_status === activeHealthFilter
    )
  }, [activeHealthFilter, pmTaskHealthMetrics, isCompletedStatus])

  const buildPmReason = useCallback((item: any) => {
    const parts: string[] = []
    const assignee = item.assignee || item.assignee_name || null
    const status = String(item.status || "").toLowerCase().replace(/\s+/g, "_")

    if (!assignee) parts.push("Unassigned")

    const startRaw = item.start_date || null
    if (!startRaw) {
      parts.push("No start date")
    } else {
      const start = new Date(startRaw)
      if (!Number.isNaN(start.getTime()) && ["todo", "open", "new", "backlog"].includes(status)) {
        const now = new Date()
        const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
        const startDay = new Date(start.getFullYear(), start.getMonth(), start.getDate())
        const delayDays = Math.floor((startOfToday.getTime() - startDay.getTime()) / (24 * 60 * 60 * 1000))
        if (delayDays > 0) parts.push(`Start missed by ${delayDays} day(s)`)
      }
    }

    const dueRaw = item.due_date || null
    if (!dueRaw) {
      parts.push("No due date")
    } else {
      const due = new Date(dueRaw)
      if (!Number.isNaN(due.getTime())) {
        const now = new Date()
        const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
        const dueDay = new Date(due.getFullYear(), due.getMonth(), due.getDate())
        const diffDays = Math.floor((startToday.getTime() - dueDay.getTime()) / (24 * 60 * 60 * 1000))
        if (diffDays > 0) parts.push(`Overdue by ${diffDays} day(s)`)
      }
    }

    const baseReason = item.health_reason || ""
    if (baseReason) parts.push(baseReason)

    if (parts.length === 0) {
      if (item.health_status === "critical") return "Critical due to unresolved blockers."
      if (item.health_status === "at_risk") return "At risk due to delivery risk."
      return "On track."
    }

    return parts.join(" - ")
  }, [])

  const isFlatTaskMode = useMemo(() => {
    return !!(healthHierarchy?.tasks && !healthHierarchy?.epics && !healthHierarchy?.sprints)
  }, [healthHierarchy])

  const flatTaskGroups = useMemo(() => {
    if (!isFlatTaskMode) return null
    const tasks = Array.isArray(healthHierarchy?.tasks) ? healthHierarchy.tasks : []
    const groups = {
      overdue: [] as any[],
      dueToday: [] as any[],
      dueSoon: [] as any[],
      dueThisWeek: [] as any[],
      dueNextWeek: [] as any[],
      later: [] as any[],
      noDueDate: [] as any[],
      completed: [] as any[],
    }

    const now = new Date()
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const endOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999)
    const in3Days = new Date(endOfToday)
    in3Days.setDate(in3Days.getDate() + 3)
    const endOfWeek = new Date(endOfToday)
    endOfWeek.setDate(endOfWeek.getDate() + (7 - endOfToday.getDay()))
    const endOfNextWeek = new Date(endOfWeek)
    endOfNextWeek.setDate(endOfNextWeek.getDate() + 7)

    for (const task of tasks) {
      if (isCompletedStatus(task.status)) {
        groups.completed.push(task)
        continue
      }

      const due = task.due_date ? new Date(task.due_date) : null
      if (!due || Number.isNaN(due.getTime())) {
        groups.noDueDate.push(task)
        continue
      }

      if (due < startOfToday) groups.overdue.push(task)
      else if (due <= endOfToday) groups.dueToday.push(task)
      else if (due <= in3Days) groups.dueSoon.push(task)
      else if (due <= endOfWeek) groups.dueThisWeek.push(task)
      else if (due <= endOfNextWeek) groups.dueNextWeek.push(task)
      else groups.later.push(task)
    }

    const byDueDate = (a: any, b: any) => {
      const aTime = a?.due_date ? new Date(a.due_date).getTime() : Number.MAX_SAFE_INTEGER
      const bTime = b?.due_date ? new Date(b.due_date).getTime() : Number.MAX_SAFE_INTEGER
      if (aTime !== bTime) return aTime - bTime
      return (a?.title || "").localeCompare(b?.title || "")
    }

    groups.overdue.sort(byDueDate)
    groups.dueToday.sort(byDueDate)
    groups.dueSoon.sort(byDueDate)
    groups.dueThisWeek.sort(byDueDate)
    groups.dueNextWeek.sort(byDueDate)
    groups.later.sort(byDueDate)
    groups.noDueDate.sort((a: any, b: any) => (a?.title || "").localeCompare(b?.title || ""))
    groups.completed.sort((a: any, b: any) => (a?.title || "").localeCompare(b?.title || ""))

    return groups
  }, [healthHierarchy, isCompletedStatus, isFlatTaskMode])

  const todayFocusTasks = useMemo(() => {
    const mapped = new Map<string, { task_key: string; title: string; assignee_name?: string; url?: string; highlight: "overdue" | "due_today" }>()

    overdueTasks.forEach((task) => {
      const key = task.task_key || task.title
      mapped.set(key, {
        task_key: task.task_key || key,
        title: task.title || key,
        assignee_name: task.assignee_name,
        url: task.url,
        highlight: "overdue",
      })
    })

    tasksToday.forEach((task) => {
      const key = task.task_key || task.title
      if (mapped.has(key)) return
      mapped.set(key, {
        task_key: task.task_key || key,
        title: task.title || key,
        assignee_name: task.assignee_name,
        url: task.url,
        highlight: "due_today",
      })
    })

    return Array.from(mapped.values())
  }, [tasksToday, overdueTasks])

  const tomorrowFocusTasks = useMemo(() => {
    return tasksTomorrow.map((task) => ({
      task_key: task.task_key || task.title,
      title: task.title || task.task_key,
      assignee_name: task.assignee_name,
      url: task.url,
      highlight: "due_tomorrow" as const,
    }))
  }, [tasksTomorrow])

  const inboxCounts = useMemo(() => {
    const people = pendingInvites.length + orphanAssignees.length
    const jiraIntegration = getIntegration(selectedProject, "jira")
    const slackIntegration = getIntegration(selectedProject, "slack")
    const setup = (jiraIntegration?.status === "connected" ? 0 : 1) + (slackIntegration?.status === "connected" ? 0 : 1)
    const governance = charterStatus && (
      charterStatus.stages?.goal !== "finalized" ||
      charterStatus.stages?.scope !== "finalized" ||
      charterStatus.stages?.requirements !== "finalized" ||
      charterStatus.stages?.features_tasks !== "finalized"
    ) ? 1 : 0

    return { people, setup, governance }
  }, [pendingInvites, orphanAssignees, selectedProject, charterStatus])

  const firstName = userName?.split(" ")[0] || "there"
  const pmBoardSummaryCacheRef = useRef<Map<string, { fetchedAt: number; data: any }>>(new Map())
  const pmBoardSummaryInFlightRef = useRef<Map<string, Promise<any>>>(new Map())
  const pmBoardSummaryAbortRef = useRef<AbortController | null>(null)
  const pmBoardStatusPollAttemptRef = useRef(0)
  const pmBoardProcessingStartedAtRef = useRef<number | null>(null)
  const lastPmBoardProjectIdRef = useRef<string | null>(null)
  const memberDefaultLandingAppliedRef = useRef(false)
  const pmBoardClientCacheTtlMs = useMemo(() => {
    const raw = Number(process.env.NEXT_PUBLIC_PM_BOARD_CLIENT_CACHE_TTL_MS)
    return Number.isFinite(raw) && raw >= 10_000 ? raw : 300_000
  }, [])
  const showPmBoardInitialLoading = activeNav === "PM Board" && !pmBoardSummaryLoaded
  const showPmBoardBackgroundRefreshing = activeNav === "PM Board" && pmBoardSummaryLoaded && pulseLoading
  const pmInitialLoadingMessages = useMemo(
    () => [
      "AI agents are analyzing your latest project signals...",
      "Preparing your project intelligence...",
      "Loading actionable insights for your project...",
    ],
    []
  )
  const [pmInitialLoadingMessageIndex, setPmInitialLoadingMessageIndex] = useState(0)

  const readPmBoardBrowserCache = useCallback((projectId: string) => {
    if (typeof window === "undefined" || !projectId) return null
    try {
      const raw = window.sessionStorage.getItem(`pm-board-summary:${projectId}`)
      if (!raw) return null
      const parsed = JSON.parse(raw)
      const fetchedAt = Number(parsed?.fetchedAt || 0)
      if (!Number.isFinite(fetchedAt) || !parsed?.data) return null
      if (Date.now() - fetchedAt > pmBoardClientCacheTtlMs) return null
      return { fetchedAt, data: parsed.data }
    } catch {
      return null
    }
  }, [pmBoardClientCacheTtlMs])

  const writePmBoardBrowserCache = useCallback((projectId: string, payload: { fetchedAt: number; data: any }) => {
    if (typeof window === "undefined" || !projectId) return
    try {
      window.sessionStorage.setItem(`pm-board-summary:${projectId}`, JSON.stringify(payload))
    } catch {
      // ignore browser storage failures
    }
  }, [])

  const clearPmBoardBrowserCache = useCallback((projectId: string) => {
    if (typeof window === "undefined" || !projectId) return
    try {
      window.sessionStorage.removeItem(`pm-board-summary:${projectId}`)
    } catch {
      // ignore browser storage failures
    }
    pmBoardSummaryCacheRef.current.delete(`${projectId}:PM Board`)
  }, [])

  useEffect(() => {
    if (!showPmBoardInitialLoading || activeNav !== "PM Board") return
    const timer = window.setInterval(() => {
      setPmInitialLoadingMessageIndex((prev) => (prev + 1) % pmInitialLoadingMessages.length)
    }, 2400)
    return () => window.clearInterval(timer)
  }, [showPmBoardInitialLoading, activeNav, pmInitialLoadingMessages.length])

  useEffect(() => {
    const fetchTimezones = async () => {
      try {
        const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
        const response = await backendFetch(`${backendUrl}/api/projects/timezones`, {
          cache: "no-store",
        })
        if (!response.ok) {
          throw new Error(`timezone_fetch_failed status=${response.status}`)
        }
        const data = await response.json()
        if (Array.isArray(data) && data.length > 0) {
          setAvailableTimezones(data.filter((tz) => typeof tz === "string"))
        }
      } catch (error) {
        console.error("Failed to fetch global timezone list:", error)
      }
    }
    fetchTimezones()
  }, [])

  // Save selected project to cookie (persists across all refreshes)
  useEffect(() => {
    if (selectedProject?._id) {
      document.cookie = `lastSelectedProjectId=${selectedProject._id}; path=/; max-age=31536000`
    }
  }, [selectedProject])

  useEffect(() => {
    setExpandedItems(new Set())
    setShowFlatCompleted(false)
    setCollapsedFlatSections({})
    setActiveHealthFilter(null)
  }, [selectedProject?._id])

  // URL is source-of-truth for nav/tab so deep links and PM Board card redirects remain stable.
  useEffect(() => {
    const nav = searchParams.get("nav")
    const tab = searchParams.get("tab")
    if (!nav) return
    if (nav === "Control Panel" && !isSelectedProjectOwner) {
      setActiveNav("Pulse")
      setActivePulseTab("Action Items")
      return
    }
    setActiveNav((prev) => (prev === nav ? prev : nav))
    if (nav === "Pulse" && tab) {
      const normalizedTab = normalizePulseTab(tab)
      setActivePulseTab((prev) => (prev === normalizedTab ? prev : normalizedTab))
      if (normalizedTab === "Work Item") {
        pulseHealthNavIntentRef.current = false
      }
    }
    if (nav === "Control Panel" && tab) {
      setActiveControlPanelTab((prev) => (prev === tab ? prev : tab))
      pulseHealthNavIntentRef.current = false
    }
    if (nav !== "Pulse") {
      pulseHealthNavIntentRef.current = false
    }
  }, [isSelectedProjectOwner, searchParams])

  // Auto-collapse Quick Start Guide when communication + work item categories are connected
  useEffect(() => {
    if (isToolsConnected(selectedProject)) {
      setIsQuickActionsExpanded(false)
    }
  }, [selectedProject, isToolsConnected])

  useEffect(() => {
    return () => {
      if (deleteRedirectTimeoutRef.current !== null) {
        window.clearTimeout(deleteRedirectTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (isSelectedProjectOwner) return
    if (activeNav !== "Control Panel") return
    setActiveNav("Pulse")
    setActivePulseTab("Action Items")
  }, [activeNav, isSelectedProjectOwner])

  // Keep URL in sync with current nav/tab/project without dropping existing query filters.
  // Only navs that support tabs (Pulse, Control Panel) should drive the `tab` query param.
  useEffect(() => {
    const currentParams = new URLSearchParams(window.location.search)
    const currentNav = currentParams.get('nav')
    const currentTab = currentParams.get('tab')
    const currentProjectId = currentParams.get('projectId')
    const currentHealthStatus = currentParams.get('healthStatus')
    const effectiveNav = !isSelectedProjectOwner && activeNav === "Control Panel" ? "Pulse" : activeNav
    const supportsTab = effectiveNav === "Pulse" || effectiveNav === "Control Panel"
    const targetTab = effectiveNav === "Pulse"
      ? activePulseTab
      : effectiveNav === "Control Panel"
        ? activeControlPanelTab
        : null

    // During PM Board -> Pulse health deep-link transition, never let stale local PM state
    // overwrite the URL while search params state is still converging.
    if (
      pulseHealthNavIntentRef.current &&
      currentNav === "Pulse" &&
      normalizePulseTab(currentTab) === "Work Item"
    ) {
      return
    }

    // Update URL if current state doesn't match URL params
    if (
      effectiveNav !== currentNav ||
      (supportsTab ? targetTab !== currentTab : currentTab !== null) ||
      selectedProject?._id !== currentProjectId
    ) {
      // Use the live URL as the source of truth to avoid stale-searchParams races.
      const params = new URLSearchParams(window.location.search)

      // Remove integration-related params that shouldn't persist.
      // Keep Slack callback params until the dedicated Slack callback effect
      // consumes them to auto-open the channel setup modal.
      params.delete('jira_pending_selection')
      params.delete('integration')
      params.delete('status')
      params.delete('error')
      if (!(activeNav === "Pulse" && targetTab === "Work Item")) {
        params.delete('healthStatus')
        params.delete('workItemView')
        params.delete('recommendationCategory')
        params.delete('quickFocus')
      }

      params.set('nav', effectiveNav)
      if (supportsTab && targetTab) {
        params.set('tab', targetTab)
      } else {
        params.delete('tab')
      }
      if (selectedProject?._id) {
        params.set('projectId', selectedProject._id)
      }

      const nextUrl = `/projects?${params.toString()}`
      window.history.replaceState({}, "", nextUrl)
    }
  }, [activeNav, activeControlPanelTab, activePulseTab, isSelectedProjectOwner, selectedProject])

  // URL hygiene: `healthStatus` is valid only on Pulse > Work Item.
  // Strip it from all other nav/tab routes to prevent sticky cross-page filters.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const nav = params.get("nav")
    const tab = params.get("tab")
    const healthStatus = params.get("healthStatus")
    if (!healthStatus) return
    const allowHealthStatus = nav === "Pulse" && normalizePulseTab(tab) === "Work Item"
    if (allowHealthStatus) return
    params.delete("healthStatus")
    const nextUrl = `/projects?${params.toString()}`
    window.history.replaceState({}, "", nextUrl)
  }, [searchParams])

  // Refresh PM Board immediately when entering board/project and every 5 min while visible
  useEffect(() => {
    if (!selectedProject || activeNav !== "PM Board") return
    const isProjectSwitch = lastPmBoardProjectIdRef.current !== selectedProject._id
    setPmBoardRefreshTrigger(isProjectSwitch ? "project_switch" : "poll")
    setPmBoardRefreshNonce((prev) => prev + 1)
    if (isProjectSwitch) {
      setPmBoardSummaryLoaded(false)
      setPmBoardSummaryProcessing(false)
      setPmBoardSummaryError(null)
      pmBoardStatusPollAttemptRef.current = 0
      pmBoardProcessingStartedAtRef.current = null
    }
    lastPmBoardProjectIdRef.current = selectedProject._id
  }, [selectedProject?._id, activeNav])

  useEffect(() => {
    if (!selectedProject || activeNav !== "PM Board") return

    const refreshIfVisible = () => {
      if (document.visibilityState === "visible") {
        triggerPmBoardRefresh(false, "poll")
      }
    }

    const intervalId = window.setInterval(refreshIfVisible, 5 * 60 * 1000)
    document.addEventListener("visibilitychange", refreshIfVisible)
    return () => {
      window.clearInterval(intervalId)
      document.removeEventListener("visibilitychange", refreshIfVisible)
    }
  }, [selectedProject, activeNav, triggerPmBoardRefresh])

  useEffect(() => {
    if (!pmBoardRefreshing) return
    const timeoutId = window.setTimeout(() => {
      setPmBoardRefreshing(false)
    }, 10000)
    return () => window.clearTimeout(timeoutId)
  }, [pmBoardRefreshing])


  // Check for integration success/error in URL and handle reload
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const integration = params.get('integration')
    const status = params.get('status')
    const error = params.get('error')
    const nav = params.get('nav')
    const tab = params.get('tab')
    const slackConnected = params.get('slack_connected')
    const jiraPendingSelection = params.get('jira_pending_selection')
    const projectIdParam = params.get('project_id')

    // Handle Jira project selection modal (only from OAuth, not on reload)
    if (jiraPendingSelection === 'true' && projectIdParam) {
      // Set navigation state only if not already set from props
      if (nav && !initialNav) {
        setActiveNav(nav)
      }
      if (tab && !initialTab) {
        setActiveControlPanelTab(tab)
      }

      // Set project ID immediately
      setJiraSelectionProjectId(projectIdParam)

      // Show loading state while waiting for modal
      setIsLoadingJiraProjectSelection(true)

      // Remove only integration-related query params from URL, preserve nav/tab
      const cleanParams = new URLSearchParams(window.location.search)
      cleanParams.delete('jira_pending_selection')
      cleanParams.delete('project_id')
      const newUrl = cleanParams.toString() ? `${window.location.pathname}?${cleanParams.toString()}` : window.location.pathname
      window.history.replaceState({}, '', newUrl)

      // Open modal after 2 seconds to let user see the page
      setTimeout(() => {
        setShowJiraProjectSelectionModal(true)
        setIsLoadingJiraProjectSelection(false)
      }, 2000)
    } else if (slackConnected === 'true' && projectIdParam && !sessionStorage.getItem('slack_modal_shown')) {
      // Mark that we've shown the modal in this session
      sessionStorage.setItem('slack_modal_shown', 'true')
      // Queue modal auto-open until auth/session context is ready
      setPendingSlackAutoOpenProjectId(projectIdParam)

      // Remove only Slack-related query params from URL
      const cleanParams = new URLSearchParams(window.location.search)
      cleanParams.delete('slack_connected')
      cleanParams.delete('project_id')
      const newUrl = cleanParams.toString() ? `${window.location.pathname}?${cleanParams.toString()}` : window.location.pathname
      window.history.replaceState({}, '', newUrl)

    } else if (integration && status === 'success') {
      // Remove only integration/status query params from URL, preserve nav/tab
      const cleanParams = new URLSearchParams(window.location.search)
      cleanParams.delete('integration')
      cleanParams.delete('status')
      const newUrl = cleanParams.toString() ? `${window.location.pathname}?${cleanParams.toString()}` : window.location.pathname
      window.history.replaceState({}, '', newUrl)
      // Refresh server data without full page reload
      router.refresh()
    } else if (error) {
      // Remove error param from URL, preserve nav/tab
      const cleanParams = new URLSearchParams(window.location.search)
      cleanParams.delete('error')
      const newUrl = cleanParams.toString() ? `${window.location.pathname}?${cleanParams.toString()}` : window.location.pathname
      window.history.replaceState({}, '', newUrl)
    }
  }, [])

  useEffect(() => {
    if (!pendingSlackAutoOpenProjectId) return
    if (!effectiveBackendToken) return
    setSlackSetupProjectId(pendingSlackAutoOpenProjectId)
    setShowSlackChannelModal(true)
    setPendingSlackAutoOpenProjectId(null)
  }, [pendingSlackAutoOpenProjectId, effectiveBackendToken])

  // Fetch team members when project changes or Control Panel is accessed
  useEffect(() => {
    const fetchTeamMembers = async () => {
      if (isSelectedProjectOwner && selectedProject && activeNav === "Control Panel" && activeControlPanelTab === "Manage Team") {
        try {
          const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
          const response = await backendFetch(`${backendUrl}/api/projects/${selectedProject._id}/members`)

          if (response.ok) {
            const membersData = await response.json()
            const formattedMembers = membersData
              .filter((member: any) => String(member?.status || "").toLowerCase() !== "removed")
              .map((member: any) => ({
                id: member.user_id || member.email,
                user_id: member.user_id || "",
                name: member.name || member.email?.split("@")[0] || "Unknown",
                email: member.email || "",
                status_raw: String(member.status || "").toLowerCase(),
                status: member.status === "active" ? "Active" : member.status === "pending" ? "Invite Sent" : member.status,
                role_value: String(member.role || "").toLowerCase(),
                role: member.role === "owner" ? "Owner" : member.role === "admin" ? "Admin" : "Member"
              }))

            setTeamMembers(formattedMembers)
          } else {
            console.error("Failed to fetch team members")
          }
        } catch (error) {
          console.error("Error fetching team members:", error)
        }
      }
    }

    fetchTeamMembers()
  }, [selectedProject, activeNav, activeControlPanelTab, isSelectedProjectOwner])

  // Helper function to extract active sprint from health data
  const extractActiveSprintFromData = (healthData: any) => {
    // Check in epics/sprints/tasks for active sprint
    const items = healthData.epics || healthData.sprints || healthData.tasks || []

    for (const item of items) {
      // For sprint-based health, items ARE sprints (check sprint_state directly)
      // For epic/task-based health, items have sprint field (check item.sprint)
      if (item.sprint_state === 'active') {
        return {
          name: item.title || item.sprint,  // sprint-based: use title, epic/task-based: use sprint
          startDate: item.sprint_start_date,
          endDate: item.sprint_end_date
        }
      }

      // Fallback: check if item has sprint field (for epic/task-based health)
      if (item.sprint && item.sprint_state === 'active') {
        return {
          name: item.sprint,
          startDate: item.sprint_start_date,
          endDate: item.sprint_end_date
        }
      }

      // Check nested items (stories/tasks within epics)
      if (item.stories) {
        for (const story of item.stories) {
          if (story.sprint && story.sprint_state === 'active') {
            return {
              name: story.sprint,
              startDate: story.sprint_start_date,
              endDate: story.sprint_end_date
            }
          }
        }
      }

      if (item.tasks) {
        for (const task of item.tasks) {
          if (task.sprint && task.sprint_state === 'active') {
            return {
              name: task.sprint,
              startDate: task.sprint_start_date,
              endDate: task.sprint_end_date
            }
          }
        }
      }
    }

    return null
  }

  const applyPmBoardSummaryData = useCallback((data: any) => {
    const health = data.health || {}
    const summary = health.summary || {}
    const workItemSummary = health.work_item_summary || {}
    const healthType = health.health_type || "task"
    let displayLabel = "Tasks"
    if (healthType === "epic") displayLabel = "Epics"
    else if (healthType === "sprint") displayLabel = "Sprints"

    setProjectHealth({
      healthType,
      onTrack: workItemSummary.on_track ?? summary.on_track ?? 0,
      atRisk: workItemSummary.at_risk ?? summary.at_risk ?? 0,
      critical: workItemSummary.critical ?? summary.critical ?? 0,
      total: workItemSummary.total ?? summary.total_epics ?? summary.total_sprints ?? summary.total_tasks ?? 0,
      displayLabel,
    })
    if (health.epics || health.sprints || health.tasks || health.work_items) {
      setHealthHierarchy(health)
    } else {
      setHealthHierarchy(null)
    }

    if (selectedProject?.workflow_capabilities?.has_sprints) {
      const activeSprint = extractActiveSprintFromData(health)
      setActiveSprint(activeSprint || null)
    }

    const tasks = data.tasks || {}
    setOverdueTasks(Array.isArray(tasks.overdue) ? tasks.overdue : [])
    setTasksToday(Array.isArray(tasks.today) ? tasks.today : [])
    setTasksTomorrow(Array.isArray(tasks.tomorrow) ? tasks.tomorrow : [])
    setBottlenecks(Array.isArray(tasks.bottlenecks) ? tasks.bottlenecks : [])
    setTasksDue24h(Array.isArray(tasks.due_24h) ? tasks.due_24h : [])
    setTomorrowAvailability(Array.isArray(tasks.tomorrow_availability) ? tasks.tomorrow_availability : [])
    setUnassignedUrgent(Array.isArray(tasks.unassigned_urgent) ? tasks.unassigned_urgent : [])
    setSilentTasks(Array.isArray(tasks.silent) ? tasks.silent : [])
    setStaleReviews(Array.isArray(tasks.stale_reviews) ? tasks.stale_reviews : [])
    setActionItemOpenCount(Math.max(0, Number(data?.action_items?.open_count) || 0))

    const review = data.review || {}
    setPendingInvites(Array.isArray(review.pending_invites) ? review.pending_invites : [])
    setOrphanAssignees(Array.isArray(review.orphan_assignees) ? review.orphan_assignees : [])

    setBackendRecommendations(normalizeBackendRecommendations(data.recommendations))

    setCharterStatus(data.charter_status || null)

    const forecast = data.forecast || {}
    if (forecast.burndown !== undefined) setBurndownData(forecast.burndown)
    if (forecast.velocity !== undefined) setVelocityData(forecast.velocity)
  }, [selectedProject])

  const effectiveAlerts = useMemo(() => {
    return backendRecommendations
  }, [backendRecommendations])

  const recommendationByTaskKey = useMemo(() => {
    const map: Record<string, string> = {}
    effectiveAlerts.forEach((alert) => {
      if (!alert.taskKey) return
      if (!map[alert.taskKey]) {
        map[alert.taskKey] = alert.recommendation
      }
    })
    return map
  }, [effectiveAlerts])

  const teamMemberNameById = useMemo(() => {
    const map: Record<string, string> = {}
    const projectMembers = Array.isArray((selectedProject as any)?.members) ? (selectedProject as any).members : []
    projectMembers.forEach((member: any) => {
      const key = String(member?.user_id || "").trim()
      const name = String(member?.name || member?.email || "").trim()
      if (key && name && !map[key]) map[key] = name
    })
    teamMembers.forEach((member) => {
      const key = String(member?.id || "").trim()
      const name = String(member?.name || "").trim()
      if (key && name && !map[key]) map[key] = name
    })
    return map
  }, [selectedProject, teamMembers])

  const activeProjectMembers = useMemo(() => {
    const projectMembers = Array.isArray((selectedProject as any)?.members) ? (selectedProject as any).members : []
    return projectMembers
      .filter((member: any) => String(member?.status || "").trim().toLowerCase() === "active")
      .map((member: any) => {
        const memberId = String(member?.user_id || "").trim()
        const memberName = String(
          member?.name || member?.email || teamMemberNameById[memberId] || memberId
        ).trim()
        return {
          id: memberId,
          name: memberName,
        }
      })
      .filter((member: any) => Boolean(member.id))
  }, [selectedProject, teamMemberNameById])

  const currentUserRole = useMemo(() => {
    if (String((selectedProject as any)?.user_id || "").trim() === String(userId || "").trim()) {
      return "owner"
    }
    const projectMembers = Array.isArray((selectedProject as any)?.members) ? (selectedProject as any).members : []
    const currentFromProject = projectMembers.find((member: any) =>
      String(member?.user_id || "").trim() === String(userId || "").trim() &&
      String(member?.status || "").trim().toLowerCase() === "active"
    )
    const roleFromProject = String(currentFromProject?.role || "").trim().toLowerCase()
    if (roleFromProject) return roleFromProject

    const currentFromTeam = teamMembers.find((member: any) => String(member?.id || "").trim() === String(userId || "").trim())
    const roleFromTeam = String(currentFromTeam?.role || "").trim().toLowerCase()
    if (roleFromTeam === "owner" || roleFromTeam === "admin" || roleFromTeam === "member") return roleFromTeam
    return "member"
  }, [selectedProject, teamMembers, userId])

  const isExplicitProjectMemberRole = useMemo(() => {
    const projectMembers = Array.isArray((selectedProject as any)?.members) ? (selectedProject as any).members : []
    const projectMatch = projectMembers.find((member: any) =>
      String(member?.user_id || "").trim() === String(userId || "").trim() &&
      String(member?.status || "").trim().toLowerCase() === "active"
    )
    const projectRole = String(projectMatch?.role || "").trim().toLowerCase()
    if (projectRole === "member") return true

    const teamMatch = teamMembers.find((member: any) => String(member?.id || "").trim() === String(userId || "").trim())
    const teamRole = String(teamMatch?.role || "").trim().toLowerCase()
    return teamRole === "member"
  }, [selectedProject, teamMembers, userId])

  useEffect(() => {
    if (memberDefaultLandingAppliedRef.current) return
    if (!selectedProject?._id) return

    const navParam = searchParams.get("nav")
    const tabParam = searchParams.get("tab")
    const hasExplicitNavigation = Boolean(navParam || tabParam || initialNav || initialTab)
    if (hasExplicitNavigation) {
      memberDefaultLandingAppliedRef.current = true
      return
    }

    if (currentUserRole === "member" && isExplicitProjectMemberRole) {
      setActiveNav("Pulse")
      setActivePulseTab("Action Items")
    }
    memberDefaultLandingAppliedRef.current = true
  }, [
    currentUserRole,
    initialNav,
    initialTab,
    isExplicitProjectMemberRole,
    searchParams,
    selectedProject?._id,
  ])

  const canViewAllActionItems = currentUserRole === "owner" || currentUserRole === "admin"
  const canDeleteActionItems = currentUserRole === "owner"
  const isProjectOwner = isSelectedProjectOwner
  const canAccessControlPanel = isProjectOwner

  const openControlPanelTab = useCallback((tab: string) => {
    if (!canAccessControlPanel) {
      setActiveNav("Pulse")
      setActivePulseTab("Action Items")
      return
    }
    setActiveNav("Control Panel")
    setActiveControlPanelTab(tab)
  }, [canAccessControlPanel])

  const filteredPulseActionItems = useMemo(() => {
    const now = Date.now()
    const dayMs = 24 * 60 * 60 * 1000

    return pulseActionItems.filter((item: any) => {
      const statusValue = String(item?.status || "").trim().toLowerCase()
      const isOpen = statusValue === "open"
      const closedAt = item?.closed_at
        ? new Date(item.closed_at).getTime()
        : (item?.updated_at ? new Date(item.updated_at).getTime() : NaN)
      const closedAgeMs = Number.isFinite(closedAt) ? (now - closedAt) : Number.POSITIVE_INFINITY

      if (pulseActionItemView === "open") return isOpen
      if (pulseActionItemView === "closed") return statusValue === "done" && closedAgeMs <= (30 * dayMs)
      if (pulseActionItemView === "cancelled") return statusValue === "cancelled" && closedAgeMs <= (30 * dayMs)
      if (pulseActionItemView === "archive") return !isOpen && closedAgeMs > (30 * dayMs)
      return isOpen
    })
  }, [pulseActionItemView, pulseActionItems])

  const groupedPulseActionItems = useMemo(() => {
    const parseDisplayKey = (value: any): number => {
      const text = String(value || "").trim()
      const match = text.match(/^(?:AI|ACT)-(\d+)$/i)
      if (!match) return Number.MAX_SAFE_INTEGER
      return Number(match[1])
    }
    const grouped: Record<string, { ownerId: string; ownerLabel: string; items: any[] }> = {}
    filteredPulseActionItems.forEach((item: any) => {
      const ownerId = String(item?.owner_user_id || "unassigned").trim() || "unassigned"
      const ownerLabel = ownerId === "unassigned"
        ? "Unassigned"
        : (teamMemberNameById[ownerId] || ownerId)
      if (!grouped[ownerId]) {
        grouped[ownerId] = { ownerId, ownerLabel, items: [] }
      }
      grouped[ownerId].items.push(item)
    })

    return Object.values(grouped)
      .map((group) => ({
        ...group,
        items: [...group.items].sort((a: any, b: any) => {
          const aKey = parseDisplayKey(a?.display_key)
          const bKey = parseDisplayKey(b?.display_key)
          if (aKey !== bKey) return aKey - bKey
          const aCreated = new Date(a?.created_at || 0).getTime()
          const bCreated = new Date(b?.created_at || 0).getTime()
          return aCreated - bCreated
        }),
      }))
      .sort((a, b) => a.ownerLabel.localeCompare(b.ownerLabel))
  }, [filteredPulseActionItems, teamMemberNameById])

  const handleCloseActionItem = useCallback(async (actionItemId: string, closeStatus: "done" | "cancelled", currentStatus: string) => {
    if (!selectedProject?._id || !actionItemId) return
    const key = String(actionItemId)
    setActionItemMutationPending((prev) => ({ ...prev, [key]: true }))
    setPulseActionItemsError(null)
    try {
      const response = await backendFetch(
        `${runtimeBackendUrl}/api/projects/${selectedProject._id}/action-items/${encodeURIComponent(actionItemId)}/close`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            status: closeStatus,
          }),
        }
      )
      if (!response.ok) {
        throw new Error(`action_item_close_failed status=${response.status}`)
      }
      const updatedItem = await response.json()
      setPulseActionItems((prev) =>
        prev.map((item: any) =>
          String(item?.action_item_id || "") === key ? { ...item, ...updatedItem } : item
        )
      )
      if (String(currentStatus || "").trim().toLowerCase() === "open") {
        setActionItemOpenCount((prev) => Math.max(0, prev - 1))
      }
    } catch (error) {
      console.error("Failed to close action item:", error)
      setPulseActionItemsError("Unable to update action item right now.")
    } finally {
      setActionItemMutationPending((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }, [runtimeBackendUrl, selectedProject?._id, userId])

  const handleUpdateActionItemColor = useCallback(async (actionItemId: string, noteColor: string) => {
    if (!selectedProject?._id || !actionItemId || !ACTION_ITEM_NOTE_COLOR_CHOICES.includes(noteColor)) return
    const key = String(actionItemId)
    setActionItemMutationPending((prev) => ({ ...prev, [key]: true }))
    setPulseActionItemsError(null)
    try {
      const response = await backendFetch(
        `${runtimeBackendUrl}/api/projects/${selectedProject._id}/action-items/${encodeURIComponent(actionItemId)}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            note_color: noteColor,
          }),
        }
      )
      if (!response.ok) {
        throw new Error(`action_item_color_update_failed status=${response.status}`)
      }
      const updatedItem = await response.json()
      setPulseActionItems((prev) =>
        prev.map((item: any) =>
          String(item?.action_item_id || "") === key ? { ...item, ...updatedItem } : item
        )
      )
      setActiveActionItemColorPickerId((current) => (current === key ? null : current))
    } catch (error) {
      console.error("Failed to update action item color:", error)
      setPulseActionItemsError("Unable to update action item right now.")
    } finally {
      setActionItemMutationPending((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }, [runtimeBackendUrl, selectedProject?._id, userId])

  const handleReopenActionItem = useCallback(async (actionItemId: string, currentStatus: string) => {
    if (!selectedProject?._id || !actionItemId) return
    const key = String(actionItemId)
    setActionItemMutationPending((prev) => ({ ...prev, [key]: true }))
    setPulseActionItemsError(null)
    try {
      const response = await backendFetch(
        `${runtimeBackendUrl}/api/projects/${selectedProject._id}/action-items/${encodeURIComponent(actionItemId)}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            status: "open",
          }),
        }
      )
      if (!response.ok) {
        throw new Error(`action_item_reopen_failed status=${response.status}`)
      }
      const updatedItem = await response.json()
      setPulseActionItems((prev) =>
        prev.map((item: any) =>
          String(item?.action_item_id || "") === key ? { ...item, ...updatedItem } : item
        )
      )
      if (String(currentStatus || "").trim().toLowerCase() !== "open") {
        setActionItemOpenCount((prev) => prev + 1)
      }
    } catch (error) {
      console.error("Failed to reopen action item:", error)
      setPulseActionItemsError("Unable to reopen action item right now.")
    } finally {
      setActionItemMutationPending((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }, [runtimeBackendUrl, selectedProject?._id, userId])

  const handleDeleteActionItem = useCallback(async (actionItemId: string, currentStatus: string) => {
    if (!selectedProject?._id || !actionItemId || !canDeleteActionItems) return
    const key = String(actionItemId)
    setActionItemMutationPending((prev) => ({ ...prev, [key]: true }))
    setPulseActionItemsError(null)
    try {
      const response = await backendFetch(
        `${runtimeBackendUrl}/api/projects/${selectedProject._id}/action-items/${encodeURIComponent(actionItemId)}`,
        { method: "DELETE" }
      )
      if (!response.ok) {
        throw new Error(`action_item_delete_failed status=${response.status}`)
      }
      setPulseActionItems((prev) =>
        prev.filter((item: any) => String(item?.action_item_id || "") !== key)
      )
      if (String(currentStatus || "").trim().toLowerCase() === "open") {
        setActionItemOpenCount((prev) => Math.max(0, prev - 1))
      }
    } catch (error) {
      console.error("Failed to delete action item:", error)
      setPulseActionItemsError("Unable to delete action item right now.")
    } finally {
      setActionItemMutationPending((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }, [canDeleteActionItems, runtimeBackendUrl, selectedProject?._id, userId])

  const openAddActionItemModal = useCallback(() => {
    setAddActionItemTitle("")
    setAddActionItemDueDate("")
    setAddActionItemError(null)
    setAddActionItemSubmitting(false)
    setAddActionItemOwnerUserId(String(userId || "").trim())
    setAddActionItemColor(ACTION_ITEM_NOTE_COLOR_CHOICES[Math.floor(Math.random() * ACTION_ITEM_NOTE_COLOR_CHOICES.length)])
    setShowAddActionItemModal(true)
  }, [userId])

  const closeAddActionItemModal = useCallback(() => {
    if (addActionItemSubmitting) return
    setShowAddActionItemModal(false)
    setAddActionItemError(null)
  }, [addActionItemSubmitting])

  const handleCreateActionItemFromModal = useCallback(async () => {
    if (!selectedProject?._id) return
    const title = String(addActionItemTitle || "").trim()
    const ownerUserId = String(addActionItemOwnerUserId || "").trim()
    if (!title) {
      setAddActionItemError("Action item is required.")
      return
    }
    if (!ownerUserId) {
      setAddActionItemError("Owner is required.")
      return
    }

    setAddActionItemSubmitting(true)
    setAddActionItemError(null)
    try {
      const dueDateText = String(addActionItemDueDate || "").trim()
      const dueDateIso = dueDateText ? `${dueDateText}T00:00:00Z` : null
      const payload: any = {
        title,
        owner_user_id: ownerUserId,
        source: "manual",
        due_type: dueDateIso ? "by_date" : "no_due_date",
        due_date: dueDateIso,
        note_color: addActionItemColor,
      }
      const response = await backendFetch(
        `${runtimeBackendUrl}/api/projects/${selectedProject._id}/action-items`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        }
      )
      if (!response.ok) {
        let detail = "Unable to create action item right now."
        try {
          const errorPayload = await response.json()
          if (errorPayload?.detail) detail = String(errorPayload.detail)
        } catch {
          // Keep default fallback detail.
        }
        throw new Error(detail)
      }
      const createdItem = await response.json()
      setShowAddActionItemModal(false)
      setAddActionItemTitle("")
      setAddActionItemOwnerUserId(String(userId || "").trim())
      setAddActionItemDueDate("")
      setAddActionItemColor(ACTION_ITEM_NOTE_COLOR_CHOICES[0])
      setPulseActionItems((prev) => [createdItem, ...prev])
      setActionItemOpenCount((prev) => prev + 1)
    } catch (error) {
      const message = String((error as any)?.message || "Unable to create action item right now.")
      setAddActionItemError(message)
    } finally {
      setAddActionItemSubmitting(false)
    }
  }, [
    addActionItemDueDate,
    addActionItemColor,
    addActionItemOwnerUserId,
    addActionItemTitle,
    runtimeBackendUrl,
    selectedProject?._id,
    userId,
  ])

  useEffect(() => {
    if (!selectedProject || activeNav !== "Pulse" || activePulseTab !== "Action Items") return

    const fetchPulseActionItems = async () => {
      setPulseActionItemsLoading(true)
      setPulseActionItemsError(null)
      try {
        const params = new URLSearchParams()
        params.set("limit", "200")
        const response = await backendFetch(
          `${runtimeBackendUrl}/api/projects/${selectedProject._id}/action-items?${params.toString()}`,
          {}
        )
        if (!response.ok) {
          throw new Error(`action_items_list_failed status=${response.status}`)
        }
        const payload = await response.json()
        setPulseActionItems(Array.isArray(payload?.items) ? payload.items : [])
      } catch (error) {
        console.error("Failed to fetch pulse action items:", error)
        setPulseActionItemsError("Unable to load action items right now.")
        setPulseActionItems([])
      } finally {
        setPulseActionItemsLoading(false)
      }
    }

    fetchPulseActionItems()
  }, [
    activeNav,
    activePulseTab,
    pulseActionItemView,
    pulseActionItemsRefreshNonce,
    runtimeBackendUrl,
    selectedProject,
    userId,
  ])

  // ── Meeting Insights: fetch meetings list ──────────────────────────────────
  useEffect(() => {
    if (activeNav !== "Pulse" || activePulseTab !== "Meeting Insights") return
    if (!selectedProject) return
    const projectId = selectedProject.project_id || selectedProject._id
    if (!projectId) return

    let cancelled = false
    const fetchMeetings = async () => {
      setMeetingsLoading(true)
      setMeetingsError(null)
      try {
        const response = await backendFetch(
          `${runtimeBackendUrl}/api/integrations/meetings/list?project_id=${encodeURIComponent(projectId)}`,
          {}
        )
        if (!response.ok) throw new Error(`meetings_list_failed status=${response.status}`)
        const payload = await response.json()
        if (cancelled) return
        const items = Array.isArray(payload?.meetings) ? payload.meetings : []
        setMeetings(items)
        if (items.length > 0) {
          setSelectedMeetingId((prev) => prev && items.some((m: any) => m.session_id === prev) ? prev : items[0].session_id)
        } else {
          setSelectedMeetingId(null)
          setMeetingDetail(null)
        }
      } catch (err) {
        if (cancelled) return
        console.error("Failed to fetch meetings:", err)
        setMeetingsError("Unable to load meetings right now.")
        setMeetings([])
      } finally {
        if (!cancelled) setMeetingsLoading(false)
      }
    }
    fetchMeetings()
    return () => { cancelled = true }
  }, [activeNav, activePulseTab, selectedProject, runtimeBackendUrl, backendFetch])

  // ── Meeting Insights: fetch selected meeting detail ────────────────────────
  useEffect(() => {
    if (!selectedMeetingId || !selectedProject) {
      setMeetingDetail(null)
      return
    }
    const projectId = selectedProject.project_id || selectedProject._id
    if (!projectId) return

    let cancelled = false
    const fetchDetail = async () => {
      setMeetingDetailLoading(true)
      try {
        const response = await backendFetch(
          `${runtimeBackendUrl}/api/integrations/meetings/detail/${encodeURIComponent(selectedMeetingId)}?project_id=${encodeURIComponent(projectId)}`,
          {}
        )
        if (!response.ok) throw new Error(`meeting_detail_failed status=${response.status}`)
        const payload = await response.json()
        if (!cancelled) setMeetingDetail(payload)
      } catch (err) {
        if (cancelled) return
        console.error("Failed to fetch meeting detail:", err)
        setMeetingDetail(null)
      } finally {
        if (!cancelled) setMeetingDetailLoading(false)
      }
    }
    fetchDetail()
    return () => { cancelled = true }
  }, [selectedMeetingId, selectedProject, runtimeBackendUrl, backendFetch])

  // Composed PM Board/Pulse fetch to avoid endpoint fan-out on every refresh/navigation.
  useEffect(() => {
    let localController: AbortController | null = null
    let requestTimeoutId: number | null = null
    const isAbortError = (error: unknown) => {
      if (error === "effect_cleanup" || error === "stale_request" || error === "request_timeout") return true
      const name = (error as any)?.name
      const code = (error as any)?.code
      const message = String((error as any)?.message || "")
      const raw = String(error || "")
      return (
        name === "AbortError" ||
        code === 20 ||
        message.toLowerCase().includes("aborted") ||
        raw === "effect_cleanup" ||
        raw === "stale_request" ||
        raw === "request_timeout"
      )
    }

    const fetchPmBoardSummary = async () => {
      if (!selectedProject) return
      const cacheKey = `${selectedProject._id}:pm-board-summary`
      const cacheTtlMs = pmBoardClientCacheTtlMs
      const now = Date.now()

      let cached = pmBoardSummaryCacheRef.current.get(cacheKey)
      if (!cached) {
        const browserCached = readPmBoardBrowserCache(selectedProject._id)
        if (browserCached) {
          cached = browserCached
          pmBoardSummaryCacheRef.current.set(cacheKey, browserCached)
        }
      }
      if (cached && (now - cached.fetchedAt) <= cacheTtlMs && !pmBoardRefreshing && activeNav !== "PM Board") {
        applyPmBoardSummaryData(cached.data)
        markPmBoardUpdated()
        setPmBoardSummaryLoaded(true)
        setPmBoardSummaryProcessing(false)
        pmBoardStatusPollAttemptRef.current = 0
        pmBoardProcessingStartedAtRef.current = null
        setPmBoardSummaryError(null)
        return
      }

      const existing = pmBoardSummaryInFlightRef.current.get(cacheKey)
      if (existing) {
        setPulseLoading(true)
        try {
          const data = await existing
          applyPmBoardSummaryData(data)
          markPmBoardUpdated()
          setPmBoardSummaryLoaded(true)
          setPmBoardSummaryProcessing(false)
          pmBoardStatusPollAttemptRef.current = 0
          pmBoardProcessingStartedAtRef.current = null
          setPmBoardSummaryError(null)
        } catch (error) {
          if (isAbortError(error)) return
          console.error("Error awaiting in-flight PM board summary:", error)
        } finally {
          setPulseLoading(false)
        }
        return
      }

      if (pmBoardSummaryAbortRef.current) {
        try {
          pmBoardSummaryAbortRef.current.abort("stale_request")
        } catch {
          // no-op
        }
      }
      const controller = new AbortController()
      localController = controller
      pmBoardSummaryAbortRef.current = controller
      setPulseLoading(true)
      setPmBoardSummaryError(null)
      try {
        requestTimeoutId = window.setTimeout(() => {
          if (!controller.signal.aborted) {
            controller.abort("request_timeout")
          }
        }, 45000)
        const summaryUrl = `${runtimeBackendUrl}/api/projects/${selectedProject._id}/pm-board-summary?trigger=${encodeURIComponent(pmBoardRefreshTrigger)}`
        const fetchPromise = backendFetch(summaryUrl, {
          signal: controller.signal,
        }).then(async (response) => {
          if (response.status === 202) {
            const payload = await response.json().catch(() => ({}))
            return { __pm_processing__: true, payload }
          }
          if (!response.ok) {
            throw new Error(`pm_board_summary_failed status=${response.status}`)
          }
          return await response.json()
        })
        pmBoardSummaryInFlightRef.current.set(cacheKey, fetchPromise)
        const data = await fetchPromise
        if ((data as any)?.__pm_processing__ === true) {
          setPmBoardSummaryProcessing(true)
          if (!pmBoardProcessingStartedAtRef.current) {
            pmBoardProcessingStartedAtRef.current = Date.now()
          }
          pmBoardStatusPollAttemptRef.current = 0
          setPmBoardSummaryError(null)
          return
        }
        const cachePayload = { fetchedAt: Date.now(), data }
        pmBoardSummaryCacheRef.current.set(cacheKey, cachePayload)
        writePmBoardBrowserCache(selectedProject._id, cachePayload)
        applyPmBoardSummaryData(data)

        markPmBoardUpdated()
        setPmBoardSummaryLoaded(true)
        setPmBoardSummaryProcessing(false)
        pmBoardStatusPollAttemptRef.current = 0
        pmBoardProcessingStartedAtRef.current = null
        setPmBoardSummaryError(null)
      } catch (error) {
        if (isAbortError(error)) return
        console.error("Error fetching PM board summary:", error)
        setPmBoardSummaryProcessing(false)
        pmBoardStatusPollAttemptRef.current = 0
        pmBoardProcessingStartedAtRef.current = null
        setPmBoardSummaryError("Sync delayed. Retrying...")
      } finally {
        if (requestTimeoutId) {
          window.clearTimeout(requestTimeoutId)
          requestTimeoutId = null
        }
        pmBoardSummaryInFlightRef.current.delete(cacheKey)
        if (pmBoardSummaryAbortRef.current === controller) {
          pmBoardSummaryAbortRef.current = null
        }
        setPulseLoading(false)
      }
    }

    fetchPmBoardSummary()
    return () => {
      if (requestTimeoutId) {
        window.clearTimeout(requestTimeoutId)
        requestTimeoutId = null
      }
      if (localController) {
        try {
          if (!localController.signal.aborted) {
            localController.abort("effect_cleanup")
          }
        } catch {
          // no-op
        }
        if (pmBoardSummaryAbortRef.current === localController) {
          pmBoardSummaryAbortRef.current = null
        }
      }
    }
  }, [selectedProject, activeNav, pmBoardRefreshNonce, markPmBoardUpdated, runtimeBackendUrl, applyPmBoardSummaryData, pmBoardRefreshing, pmBoardRefreshTrigger, pmBoardClientCacheTtlMs, readPmBoardBrowserCache, writePmBoardBrowserCache])

  // Poll async summary status while backend job is processing.
  useEffect(() => {
    if (!selectedProject || activeNav !== "PM Board") return
    if (!pmBoardSummaryProcessing) return

    let cancelled = false
    let pollTimeoutId: number | null = null

    const pollStatus = async () => {
      if (cancelled) return
      if (document.visibilityState !== "visible") {
        pollTimeoutId = window.setTimeout(pollStatus, 2000)
        return
      }
      const startedAt = pmBoardProcessingStartedAtRef.current
      if (startedAt && Date.now() - startedAt > 45_000) {
        setPmBoardSummaryProcessing(false)
        pmBoardStatusPollAttemptRef.current = 0
        pmBoardProcessingStartedAtRef.current = null
        setPmBoardSummaryError("Sync is taking longer than expected. Please retry.")
        return
      }

      try {
        const statusUrl = `${runtimeBackendUrl}/api/projects/${selectedProject._id}/pm-board-summary-status`
        const response = await backendFetch(statusUrl)
        if (!response.ok) {
          throw new Error(`pm_board_summary_status_failed status=${response.status}`)
        }
        const payload = await response.json()
        if (cancelled) return
        const statusValue = String(payload?.status || "").toLowerCase()
        if (statusValue === "ready") {
          setPmBoardSummaryProcessing(false)
          pmBoardStatusPollAttemptRef.current = 0
          pmBoardProcessingStartedAtRef.current = null
          triggerPmBoardRefresh(false, "status_ready")
          return
        }
        if (statusValue === "failed") {
          setPmBoardSummaryProcessing(false)
          pmBoardStatusPollAttemptRef.current = 0
          pmBoardProcessingStartedAtRef.current = null
          setPmBoardSummaryError("Sync delayed. Retrying...")
          triggerPmBoardRefresh(false, "retry")
          return
        }
        const retryHint = Number(payload?.retry_after_ms)
        const attempt = pmBoardStatusPollAttemptRef.current + 1
        pmBoardStatusPollAttemptRef.current = attempt
        const backoffMs = Math.min(8000, 1000 * (2 ** Math.max(0, attempt - 1)))
        const delayMs = Number.isFinite(retryHint) && retryHint > 0
          ? Math.min(8000, Math.max(1000, retryHint))
          : backoffMs
        pollTimeoutId = window.setTimeout(pollStatus, delayMs)
        return
      } catch (error) {
        if (cancelled) return
        console.error("Error polling PM board summary status:", error)
        const attempt = pmBoardStatusPollAttemptRef.current + 1
        pmBoardStatusPollAttemptRef.current = attempt
        const delayMs = Math.min(8000, 1000 * (2 ** Math.max(0, attempt - 1)))
        pollTimeoutId = window.setTimeout(pollStatus, delayMs)
        return
      }
    }

    pollStatus()
    return () => {
      cancelled = true
      if (pollTimeoutId) window.clearTimeout(pollTimeoutId)
    }
  }, [selectedProject, activeNav, pmBoardSummaryProcessing, runtimeBackendUrl, triggerPmBoardRefresh])

  // Auto-retry summary fetch while PM board is open and initial data is not available.
  useEffect(() => {
    if (!selectedProject || activeNav !== "PM Board") return
    if (pmBoardSummaryLoaded || pulseLoading || pmBoardSummaryProcessing) return
    const retryId = window.setTimeout(() => {
      triggerPmBoardRefresh(false, "retry")
    }, 3000)
    return () => window.clearTimeout(retryId)
  }, [selectedProject, activeNav, pmBoardSummaryLoaded, pulseLoading, pmBoardSummaryProcessing, triggerPmBoardRefresh])

  // Initialize Project Settings form when entering the tab
  useEffect(() => {
    const loadSettings = async () => {
      if (!(activeNav === "Control Panel" && activeControlPanelTab === "Project Settings" && selectedProject)) return

      const project = selectedProject as any
      setSettingsTimezone(project.timezone || "UTC")

      try {
        const apiBase = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
        const [cadenceResponse, actionItemDigestResponse] = await Promise.all([
          backendFetch(`${apiBase}/api/projects/${selectedProject._id}/cadence-schedule`),
          backendFetch(`${apiBase}/api/projects/${selectedProject._id}/action-item-digest-schedule`),
        ])
        if (cadenceResponse.ok) {
          const schedule = await cadenceResponse.json()
          const cadenceEnabled = schedule?.enabled !== false
          const cadenceTime = normalizeQuarterHourTime(schedule?.time || "09:00", "09:00")
          const cadenceSkipWeekends = schedule?.skip_weekends === true
          const cadenceIgnoreOwnerFollowup = schedule?.ignore_followup_for_owner === true
          setSettingsReminderEnabled(cadenceEnabled)
          setSettingsReminderTime(cadenceTime)
          setSettingsReminderSkipWeekends(cadenceSkipWeekends)
          setSettingsReminderIgnoreFollowupForOwner(cadenceIgnoreOwnerFollowup)
          setSavedReminderEnabled(cadenceEnabled)
          setSavedReminderTime(cadenceTime)
          setSavedReminderSkipWeekends(cadenceSkipWeekends)
          setSavedReminderIgnoreFollowupForOwner(cadenceIgnoreOwnerFollowup)
        } else {
          setSettingsReminderEnabled(true)
          setSettingsReminderTime("09:00")
          setSettingsReminderSkipWeekends(false)
          setSettingsReminderIgnoreFollowupForOwner(false)
          setSavedReminderEnabled(true)
          setSavedReminderTime("09:00")
          setSavedReminderSkipWeekends(false)
          setSavedReminderIgnoreFollowupForOwner(false)
        }
        if (actionItemDigestResponse.ok) {
          const digestSchedule = await actionItemDigestResponse.json()
          const digestEnabled = digestSchedule?.enabled !== false
          const digestTime = normalizeQuarterHourTime(digestSchedule?.time || "12:30", "12:30")
          const digestSkipWeekends = digestSchedule?.skip_weekends === true
          const digestIgnoreOwnerFollowup = digestSchedule?.ignore_followup_for_owner === true
          setSettingsActionItemDigestEnabled(digestEnabled)
          setSettingsActionItemDigestTime(digestTime)
          setSettingsActionItemDigestSkipWeekends(digestSkipWeekends)
          setSettingsActionItemDigestIgnoreFollowupForOwner(digestIgnoreOwnerFollowup)
          setSavedActionItemDigestEnabled(digestEnabled)
          setSavedActionItemDigestTime(digestTime)
          setSavedActionItemDigestSkipWeekends(digestSkipWeekends)
          setSavedActionItemDigestIgnoreFollowupForOwner(digestIgnoreOwnerFollowup)
        } else {
          setSettingsActionItemDigestEnabled(true)
          setSettingsActionItemDigestTime("12:30")
          setSettingsActionItemDigestSkipWeekends(false)
          setSettingsActionItemDigestIgnoreFollowupForOwner(false)
          setSavedActionItemDigestEnabled(true)
          setSavedActionItemDigestTime("12:30")
          setSavedActionItemDigestSkipWeekends(false)
          setSavedActionItemDigestIgnoreFollowupForOwner(false)
        }
      } catch (error) {
        console.error("Error loading cadence schedule:", error)
        setSettingsReminderEnabled(true)
        setSettingsReminderTime("09:00")
        setSettingsReminderSkipWeekends(false)
        setSettingsReminderIgnoreFollowupForOwner(false)
        setSavedReminderEnabled(true)
        setSavedReminderTime("09:00")
        setSavedReminderSkipWeekends(false)
        setSavedReminderIgnoreFollowupForOwner(false)
        setSettingsActionItemDigestEnabled(true)
        setSettingsActionItemDigestTime("12:30")
        setSettingsActionItemDigestSkipWeekends(false)
        setSettingsActionItemDigestIgnoreFollowupForOwner(false)
        setSavedActionItemDigestEnabled(true)
        setSavedActionItemDigestTime("12:30")
        setSavedActionItemDigestSkipWeekends(false)
        setSavedActionItemDigestIgnoreFollowupForOwner(false)
      }
      setHasUnsavedSettings(false)
    }

    loadSettings()
  }, [selectedProject, activeNav, activeControlPanelTab])

  const handleProjectChange = (projectId: string) => {
    if (projectId === "create-new") {
      // Navigate to project setup page
      window.location.href = "/project-setup"
      return
    }

    const project = sortedProjects.find((p) => p._id === projectId)
    if (project) {
      setSelectedProject(project)
      setShowProjectDropdown(false)
    }
  }

  const redirectToIntegrationConnect = (tool: string) => {
    if (!selectedProject?._id) return

    // Pass current navigation state to restore after OAuth
    const url = `/api/integrations/${tool}/connect?project_id=${selectedProject._id}&return_nav=${encodeURIComponent(activeNav)}&return_tab=${encodeURIComponent(activeControlPanelTab)}`
    window.location.href = url
  }

  const handleConnectTool = async (tool: string) => {
    if (!selectedProject?._id) return

    if (tool === "slack") {
      setIsConnectingSlack(true)
      redirectToIntegrationConnect(tool)
      return
    }

    if (tool === "jira") {
      setIsConnectingJira(true)
      try {
        const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
        const response = await backendFetch(
          `${backendUrl}/api/integrations/workitems/data-status/${selectedProject._id}`,
          { method: "GET" }
        )

        if (!response.ok) {
          let errorDetail = ""
          try {
            const errorData = await response.json()
            if (typeof errorData?.detail === "string" && errorData.detail.trim()) {
              errorDetail = errorData.detail.trim()
            }
          } catch {
            try {
              errorDetail = (await response.text()).trim()
            } catch {
              errorDetail = ""
            }
          }

          if (response.status === 404) {
            console.warn("Work-item data-status endpoint unavailable, continuing connect flow.")
            redirectToIntegrationConnect(tool)
            return
          }

          throw new Error(errorDetail || `Failed to inspect work-item data status (HTTP ${response.status})`)
        }

        const statusData = await response.json()
        const requiresCleanup = Boolean(statusData?.requires_cleanup_before_connect)
        const staleCount = Number(statusData?.work_items_count || statusData?.stale_work_items_count || 0)

        if (requiresCleanup) {
          setJiraStaleWorkItemCount(staleCount)
          setShowJiraStaleDataWarningModal(true)
          return
        }

        redirectToIntegrationConnect(tool)
      } catch (error) {
        console.error("Error checking project work-item data status:", error)
        const message = String((error as any)?.message || "Unable to verify project work-item data.")
        alert(message)
      } finally {
        setIsConnectingJira(false)
      }
    }
  }

  const executeJiraDisconnect = async (purgeData: boolean) => {
    if (!selectedProject?._id) return

    setIsSubmittingJiraDisconnectChoice(true)
    setIsDisconnectingJira(true)

    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const response = await backendFetch(
        `${backendUrl}/api/integrations/jira/disconnect/${selectedProject._id}?purge_data=${purgeData ? "true" : "false"}`,
        {
          method: "DELETE",
        }
      )

      if (response.ok) {
        setShowJiraDisconnectConfirmModal(false)
        clearPmBoardBrowserCache(selectedProject._id)
        await new Promise(resolve => setTimeout(resolve, 500))
        window.location.reload()
      } else {
        console.error("Failed to disconnect Jira")
        alert("Failed to disconnect Jira. Please try again.")
      }
    } catch (error) {
      console.error("Error disconnecting Jira:", error)
      alert("Error disconnecting Jira. Please try again.")
    } finally {
      setIsSubmittingJiraDisconnectChoice(false)
      setIsDisconnectingJira(false)
    }
  }

  const handleDisconnectTool = async (tool: string) => {
    if (!selectedProject?._id) return

    if (tool === "jira") {
      setShowJiraDisconnectConfirmModal(true)
      return
    }

    if (tool !== "slack") return

    setIsDisconnectingSlack(true)
    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const response = await backendFetch(
        `${backendUrl}/api/integrations/slack/disconnect/${selectedProject._id}`,
        {
          method: "DELETE",
        }
      )

      if (response.ok) {
        await new Promise(resolve => setTimeout(resolve, 500))
        window.location.reload()
      } else {
        console.error("Failed to disconnect Slack")
        alert("Failed to disconnect Slack. Please try again.")
      }
    } catch (error) {
      console.error("Error disconnecting Slack:", error)
      alert("Error disconnecting Slack. Please try again.")
    } finally {
      setIsDisconnectingSlack(false)
    }
  }

  const handleClearStaleJiraDataAndContinue = async () => {
    if (!selectedProject?._id) return
    setIsClearingJiraStaleData(true)
    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const response = await backendFetch(
        `${backendUrl}/api/integrations/workitems/cleanup/${selectedProject._id}`,
        {
          method: "POST",
        }
      )
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        const detail = typeof errorData?.detail === "string" ? errorData.detail : "Failed to clear existing work-item data."
        throw new Error(detail)
      }

      setShowJiraStaleDataWarningModal(false)
      setJiraStaleWorkItemCount(0)
      clearPmBoardBrowserCache(selectedProject._id)
      redirectToIntegrationConnect("jira")
    } catch (error: any) {
      const message = String(error?.message || "Failed to clear existing work-item data.")
      console.error("Error clearing existing work-item data:", error)
      alert(message)
    } finally {
      setIsClearingJiraStaleData(false)
      setIsConnectingJira(false)
    }
  }

  const handleSyncJira = async () => {
    if (!selectedProject?.project_id) return
    setIsSyncingJira(true)
    setJiraSyncError(null)

    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const response = await backendFetch(
        `${backendUrl}/api/integrations/${selectedProject.project_id}/jira/sync`,
        {
          method: "POST",
          headers: effectiveBackendToken ? { Authorization: `Bearer ${effectiveBackendToken}` } : {},
        }
      )

      if (response.ok) {
        // Show success state on button
        setJiraSyncSuccess(true)
        if (selectedProject?._id) {
          clearPmBoardBrowserCache(selectedProject._id)
        }
        router.refresh() // Refresh to show new tasks

        // Reset success state after 3 seconds
        setTimeout(() => {
          setJiraSyncSuccess(false)
        }, 3000)
      } else {
        const errorData = await response.json().catch(() => ({}))
        let errorMessage = 'Sync failed. Please try again.'

        // Make error messages more user-friendly
        if (errorData.detail) {
          const detail = errorData.detail.toLowerCase()
          if (detail.includes('session expired') || detail.includes('refresh')) {
            errorMessage = 'Jira session refreshed. Please try syncing again.'
          } else if (detail.includes('not connected')) {
            errorMessage = 'Jira not connected. Please connect Jira first.'
          } else if (detail.includes('project') && detail.includes('selected')) {
            errorMessage = 'No Jira project selected. Please select a project in settings.'
          } else {
            // Use the backend message but make it friendlier
            errorMessage = errorData.detail
          }
        }

        setJiraSyncError(errorMessage)

        // Auto-hide error after 10 seconds
        setTimeout(() => {
          setJiraSyncError(null)
        }, 10000)
      }
    } catch (error) {
      console.error("Error syncing Jira:", error)
      setJiraSyncError("Cannot reach server. Please check your connection and try again.")

      // Auto-hide error after 10 seconds
      setTimeout(() => {
        setJiraSyncError(null)
      }, 10000)
    } finally {
      setIsSyncingJira(false)
    }
  }

  const handleSyncSlack = async () => {
    if (!selectedProject?._id) return
    setIsSyncingSlack(true)

    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const response = await backendFetch(
        `${backendUrl}/api/integrations/slack/sync-members/${selectedProject._id}`,
        {
          method: "POST",
          headers: effectiveBackendToken ? { Authorization: `Bearer ${effectiveBackendToken}` } : {},
        }
      )

      if (response.ok) {
        setSlackSyncSuccess(true)
        router.refresh()

        setTimeout(() => {
          setSlackSyncSuccess(false)
        }, 3000)
      } else {
        const errorData = await response.json().catch(() => ({}))
        alert(`Failed to sync Slack members: ${errorData.detail || "Unknown error"}`)
      }
    } catch (error) {
      console.error("Error syncing Slack members:", error)
      alert("Error syncing Slack members. Please try again.")
    } finally {
      setIsSyncingSlack(false)
    }
  }

  const handleSaveSettings = async () => {
    if (!selectedProject?._id) return
    setIsSavingSettings(true)
    setSettingsSaveSuccess(false)
    const normalizedReminderTime = normalizeQuarterHourTime(settingsReminderTime, "09:00")
    const normalizedDigestTime = normalizeQuarterHourTime(settingsActionItemDigestTime, "12:30")

    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const response = await backendFetch(
        `${backendUrl}/api/projects/${selectedProject._id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            timezone: settingsTimezone
          })
        }
      )

      if (response.ok) {
        const cadenceScheduleResponse = await backendFetch(
          `${backendUrl}/api/projects/${selectedProject._id}/cadence-schedule`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              enabled: settingsReminderEnabled,
              time: normalizedReminderTime,
              skip_weekends: settingsReminderSkipWeekends,
              ignore_followup_for_owner: settingsReminderIgnoreFollowupForOwner,
            }),
          }
        )
        if (!cadenceScheduleResponse.ok) {
          console.error("Failed to save cadence schedule settings")
          return
        }
        const digestScheduleResponse = await backendFetch(
          `${backendUrl}/api/projects/${selectedProject._id}/action-item-digest-schedule`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              enabled: settingsActionItemDigestEnabled,
              time: normalizedDigestTime,
              skip_weekends: settingsActionItemDigestSkipWeekends,
              ignore_followup_for_owner: settingsActionItemDigestIgnoreFollowupForOwner,
            }),
          }
        )
        if (!digestScheduleResponse.ok) {
          console.error("Failed to save action item digest schedule settings")
          return
        }
        setSavedReminderEnabled(settingsReminderEnabled)
        setSavedReminderTime(normalizedReminderTime)
        setSavedReminderSkipWeekends(settingsReminderSkipWeekends)
        setSavedReminderIgnoreFollowupForOwner(settingsReminderIgnoreFollowupForOwner)
        setSavedActionItemDigestEnabled(settingsActionItemDigestEnabled)
        setSavedActionItemDigestTime(normalizedDigestTime)
        setSavedActionItemDigestSkipWeekends(settingsActionItemDigestSkipWeekends)
        setSavedActionItemDigestIgnoreFollowupForOwner(settingsActionItemDigestIgnoreFollowupForOwner)
        // Keep UX smooth: persist the saved state locally without hard refresh.
        setHasUnsavedSettings(false)
        setSettingsSaveSuccess(true)
        setTimeout(() => {
          setSettingsSaveSuccess(false)
        }, 3000)
      } else {
        console.error("Failed to save settings")
      }
    } catch (error) {
      console.error("Error saving settings:", error)
    } finally {
      setIsSavingSettings(false)
    }
  }

  const handleConfigureChannels = () => {
    if (!selectedProject?._id) return
    setSlackSetupProjectId(selectedProject._id)
    setShowSlackChannelModal(true)
  }

  const handleInviteMember = async () => {
    if (inviteEmail && inviteEmail.includes("@") && selectedProject) {
      setIsInviting(true)
      setInviteError("")

      try {
        const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
        const response = await backendFetch(`${backendUrl}/api/projects/${selectedProject._id}/invite`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            email: inviteEmail,
            invited_by: userId,
            role: "member",
            jira_account_id: inviteJiraAccountId
          })
        })

        if (response.ok) {
          // Refetch team members to get updated list
          const membersResponse = await backendFetch(`${backendUrl}/api/projects/${selectedProject._id}/members`)
          if (membersResponse.ok) {
            const membersData = await membersResponse.json()
            const formattedMembers = membersData
              .filter((member: any) => String(member?.status || "").toLowerCase() !== "removed")
              .map((member: any) => ({
                id: member.user_id || member.email,
                user_id: member.user_id || "",
                name: member.name || member.email?.split("@")[0] || "Unknown",
                email: member.email || "",
                status_raw: String(member.status || "").toLowerCase(),
                status: member.status === "active" ? "Active" : member.status === "pending" ? "Invite Sent" : member.status,
                role_value: String(member.role || "").toLowerCase(),
                role: member.role === "owner" ? "Owner" : member.role === "admin" ? "Admin" : "Member"
              }))
            setTeamMembers(formattedMembers)
          }

          // Refetch orphan assignees and pending invites to update Action Inbox
          const [orphanResponse, pendingMembersRes] = await Promise.all([
            backendFetch(`${backendUrl}/api/projects/${selectedProject._id}/orphan-assignees`),
            backendFetch(`${backendUrl}/api/projects/${selectedProject._id}/members`)
          ])

          let orphanData: any[] = []
          let membersData: any[] = []

          if (orphanResponse.ok) {
            orphanData = await orphanResponse.json()
          }
          if (pendingMembersRes.ok) {
            membersData = await pendingMembersRes.json()
            const pending = membersData
              .filter((m: any) => m.status === "pending")
              .map((m: any) => ({
                email: m.email,
                name: m.name || m.email?.split("@")[0] || "Unknown",
                expires_at: m.expires_at,
                invited_by: m.invited_by
              }))
            setPendingInvites(pending)
          }

          // Filter out orphans who have pending invites (by matching jira_account_id)
          const pendingJiraIds = new Set(
            membersData
              .filter((m: any) => m.status === "pending")
              .map((m: any) => m.integration_ids?.jira_account_id)
              .filter(Boolean) // Remove nulls
          )

          const filteredOrphans = orphanData.filter(
            (orphan: any) => !pendingJiraIds.has(orphan.account_id)
          )

          setOrphanAssignees(filteredOrphans)

          setInviteEmail("")
          setInviteJiraAccountId(null)
          setInviteError("")
          setShowInviteModal(false)
        } else {
          const errorData = await response.json().catch(() => ({}))
          setInviteError(errorData.detail || "Failed to send invite. Please try again.")
        }
      } catch (error) {
        console.error("Error sending invite:", error)
        setInviteError("An error occurred while sending the invite.")
      } finally {
        setIsInviting(false)
      }
    }
  }

  const handleRemindMember = async (email: string) => {
    if (!selectedProject) return
    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const response = await backendFetch(`${backendUrl}/api/projects/${selectedProject._id}/invite/remind`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email })
      })
      if (response.ok) {
        setRemindSuccess(email)
        setTimeout(() => setRemindSuccess(null), 3000)
        // Refresh pending invites
        const membersRes = await backendFetch(`${backendUrl}/api/projects/${selectedProject._id}/members`)
        if (membersRes.ok) {
          const membersData = await membersRes.json()
          const pending = membersData
            .filter((m: any) => m.status === "pending")
            .map((m: any) => ({
              email: m.email,
              name: m.name || m.email?.split("@")[0] || "Unknown",
              invited_at: m.invited_at,
              expires_at: m.expires_at,
              invited_by: m.invited_by,
              reminder_count: m.reminder_count || 0,
              last_reminded_at: m.last_reminded_at || null
            }))
          setPendingInvites(pending)
        }
      }
    } catch (error) {
      console.error("Failed to resend invite:", error)
    }
  }

  const handleStartEditMemberRole = useCallback((member: any) => {
    const userId = String(member?.user_id || "").trim()
    if (!userId) return
    const currentRole = String(member?.role_value || "").trim().toLowerCase()
    setEditingMemberRoleUserId(userId)
    setEditingMemberRoleValue(currentRole === "owner" ? "owner" : "member")
    setMemberRoleError(null)
  }, [])

  const handleCancelEditMemberRole = useCallback(() => {
    setEditingMemberRoleUserId(null)
    setMemberRoleError(null)
  }, [])

  const handleSaveMemberRole = useCallback(async () => {
    if (!selectedProject?._id || !editingMemberRoleUserId) return
    const targetMember = teamMembers.find(
      (member) => String(member?.user_id || "").trim() === editingMemberRoleUserId
    )
    if (!targetMember) return

    const currentRole = String(targetMember?.role_value || "").trim().toLowerCase()
    if (currentRole === editingMemberRoleValue) {
      setEditingMemberRoleUserId(null)
      return
    }

    const activeOwnerCount = teamMembers.filter((member) =>
      String(member?.status_raw || "").trim().toLowerCase() === "active" &&
      String(member?.role_value || "").trim().toLowerCase() === "owner"
    ).length
    if (currentRole === "owner" && editingMemberRoleValue === "member" && activeOwnerCount <= 1) {
      setMemberRoleError("At least one owner must remain in the project.")
      return
    }

    setUpdatingMemberRoleUserId(editingMemberRoleUserId)
    setMemberRoleError(null)
    try {
      const response = await backendFetch(
        `${runtimeBackendUrl}/api/projects/${selectedProject._id}/members/${encodeURIComponent(editingMemberRoleUserId)}/role`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: editingMemberRoleValue }),
        }
      )
      if (!response.ok) {
        let detail = "Failed to update member role."
        try {
          const payload = await response.json()
          if (payload?.detail) detail = String(payload.detail)
        } catch {
          // no-op
        }
        setMemberRoleError(detail)
        return
      }
      setTeamMembers((prev) =>
        prev.map((member) => {
          if (String(member?.user_id || "").trim() !== editingMemberRoleUserId) return member
          return {
            ...member,
            role_value: editingMemberRoleValue,
            role: editingMemberRoleValue === "owner" ? "Owner" : "Member",
          }
        })
      )
      setEditingMemberRoleUserId(null)
    } catch (error) {
      console.error("Failed to update member role:", error)
      setMemberRoleError("Failed to update member role.")
    } finally {
      setUpdatingMemberRoleUserId(null)
    }
  }, [backendFetch, editingMemberRoleUserId, editingMemberRoleValue, runtimeBackendUrl, selectedProject?._id, teamMembers])

  const activeOwnerCount = useMemo(
    () =>
      teamMembers.filter(
        (m: any) =>
          String(m?.role_value || "").toLowerCase() === "owner" &&
          String(m?.status_raw || "").toLowerCase() === "active"
      ).length,
    [teamMembers]
  )

  const canRemoveMember = useCallback(
    (member: any): boolean => {
      if (!member) return false
      if (!isSelectedProjectOwner) return false
      if (String(member?.status_raw || "").toLowerCase() === "removed") return false
      const isOwnerRow = String(member?.role_value || "").toLowerCase() === "owner"
      if (isOwnerRow && activeOwnerCount <= 1) return false
      return true
    },
    [activeOwnerCount, isSelectedProjectOwner]
  )

  const handleOpenRemoveMemberModal = useCallback((member: any) => {
    setRemoveMemberError(null)
    setMemberToDelete(member)
  }, [])

  const handleCancelRemoveMember = useCallback(() => {
    if (isRemovingMember) return
    setMemberToDelete(null)
    setRemoveMemberError(null)
  }, [isRemovingMember])

  const handleConfirmRemoveMember = useCallback(async () => {
    if (!memberToDelete || !selectedProject?._id) return
    const targetUserId = String(memberToDelete?.user_id || memberToDelete?.id || "").trim()
    if (!targetUserId) {
      setRemoveMemberError("Unable to identify the member to remove.")
      return
    }
    setIsRemovingMember(true)
    setRemoveMemberError(null)
    try {
      const response = await backendFetch(
        `${runtimeBackendUrl}/api/projects/${selectedProject._id}/members/${encodeURIComponent(targetUserId)}`,
        { method: "DELETE" }
      )
      if (!response.ok) {
        let detail = "Failed to remove member."
        try {
          const body = await response.json()
          if (body?.detail) detail = String(body.detail)
        } catch {}
        setRemoveMemberError(detail)
        return
      }
      setTeamMembers((prev) =>
        prev.filter((m: any) => String(m?.user_id || m?.id || "") !== targetUserId)
      )
      setMemberToDelete(null)
    } catch (error) {
      console.error("Failed to remove member:", error)
      setRemoveMemberError("Failed to remove member. Please try again.")
    } finally {
      setIsRemovingMember(false)
    }
  }, [backendFetch, memberToDelete, runtimeBackendUrl, selectedProject?._id])

  const expectedDeleteConfirmationText = useMemo(
    () => String(selectedProject?.project_name || "").trim(),
    [selectedProject?.project_name]
  )
  const canConfirmProjectDelete = Boolean(
    expectedDeleteConfirmationText &&
    deleteProjectConfirmText.trim() === expectedDeleteConfirmationText
  )

  const handleDeleteProject = useCallback(async () => {
    if (!selectedProject?._id || !isProjectOwner) return
    if (!canConfirmProjectDelete) {
      setDeleteProjectError("Type the exact project name to confirm deletion.")
      return
    }

    setIsDeletingProject(true)
    setDeleteProjectError(null)
    try {
      const response = await backendFetch(
        `${runtimeBackendUrl}/api/projects/${selectedProject._id}`,
        { method: "DELETE" }
      )

      if (response.status === 204) {
        const deletedProjectName =
          String(selectedProject?.project_name || expectedDeleteConfirmationText || "Project").trim()
        setShowDeleteProjectModal(false)
        setDeleteProjectConfirmText("")
        setDeleteProjectError(null)
        setDeleteProjectSuccessBanner(
          `Project "${deletedProjectName}" was deleted permanently. Thank you for using ProMarshal.`
        )
        if (deleteRedirectTimeoutRef.current !== null) {
          window.clearTimeout(deleteRedirectTimeoutRef.current)
        }
        deleteRedirectTimeoutRef.current = window.setTimeout(() => {
          window.location.assign("/")
        }, 3000)
        return
      }

      let errorMessage = "Failed to delete project."
      try {
        const data = await response.json()
        const detail = String(data?.detail || "").trim()
        if (detail) errorMessage = detail
      } catch {
        // no-op
      }
      if (response.status === 409) {
        errorMessage = "Delete already in progress for this project. Please retry in a moment."
      } else if (response.status === 403) {
        errorMessage = "Only the project owner can delete this project."
      } else if (response.status === 404) {
        errorMessage = "Project no longer exists."
      }
      setDeleteProjectError(errorMessage)
    } catch (error) {
      console.error("Failed to delete project:", error)
      setDeleteProjectError("Delete failed due to a network or server error.")
    } finally {
      setIsDeletingProject(false)
    }
  }, [
    backendFetch,
    canConfirmProjectDelete,
    expectedDeleteConfirmationText,
    isProjectOwner,
    runtimeBackendUrl,
    selectedProject?._id,
    selectedProject?.project_name,
  ])

  const navigationItems: { icon?: string; svg?: React.ReactNode; label: string }[] = [
    { icon: "/icons/notice-board.png", label: "PM Board" },
    // { icon: "/icons/goal.png", label: "Project Charter" }, // Temporarily hidden
    { icon: "/icons/pulse.png", label: "Pulse" },
    // { icon: "/icons/forecast.png", label: "Forecast" }, // Temporarily hidden
    // {
    //   label: "Reports",
    //   svg: (
    //     <svg viewBox="0 0 24 24" fill="none" strokeWidth={1.6} stroke="currentColor" className="w-7 h-7">
    //       <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
    //     </svg>
    //   ),
    // }, // Temporarily hidden
    // { icon: "/icons/brain.png", label: "Project Brain" }, // Temporarily hidden for demo
    ...(canAccessControlPanel ? [{ icon: "/icons/settings.png", label: "Control Panel" }] : []),
  ]

  const setupCards = [
    // { number: 1, icon: "/icons/goal.png", label: "Setup project charter" }, // Temporarily hidden for demo
    { number: 1, icon: "/icons/team.png", label: "Invite your team" },
    { number: 2, icon: "/icons/connect.png", label: "Connect your tools" },
    { number: 3, icon: "/icons/settings.png", label: "Update project settings" },
  ].filter(() => canAccessControlPanel)

  // Dark mode helpers
  // Palette: sidebar/header = #0c1422 (deep slate shell, between canvas and cards)
  //          main bg       = #020407 (near black canvas, deepest layer)
  //          cards         = #0e1521 (elevated above base, clearly visible)
  //          card headers  = #0b1019 (slightly darker accent strip)
  const dm = isDarkMode
  const shellBg = dm ? "bg-[#0c1422]" : "bg-[#d1d5db]"
  const mainBg  = dm ? "bg-[#020407]" : "bg-[#f5f7fb]"
  const cardBg  = dm ? "bg-[#0e1521]" : "bg-white"
  const cardBorder       = dm ? "border-white/[0.07]" : "border-gray-300"
  const cardHeaderBg     = dm ? "bg-[#0b1019]"        : "bg-gray-50"
  const cardHeaderBorder = dm ? "border-white/[0.06]" : "border-gray-200"
  const cardDivider      = dm ? "border-white/[0.06]" : "border-gray-200"
  const cardStyle = dm
    ? {
        background: 'linear-gradient(160deg, #0e1521 0%, #111828 50%, #0c1320 100%)',
        boxShadow: '0 0 0 1px rgba(255,255,255,0.12), inset 0 1px 0 rgba(255,255,255,0.08), 0 8px 40px rgba(0,0,0,0.60)',
      }
    : {
        background: 'linear-gradient(160deg, #ffffff 0%, #f8faff 100%)',
        boxShadow: '0 0 0 1px rgba(0,0,0,0.07), inset 0 1px 0 rgba(255,255,255,1), 0 4px 16px rgba(0,0,0,0.07)',
      }
  const cardHeaderStyle = dm
    ? { background: 'linear-gradient(90deg, #060b16 0%, #0a1020 100%)' }
    : { background: 'linear-gradient(90deg, #f9fafb 0%, #f0f4ff 100%)' }
  const textPrimary   = dm ? "text-gray-100" : "text-gray-900"
  const textSecondary = dm ? "text-gray-400" : "text-gray-500"
  const textMuted     = dm ? "text-gray-500" : "text-gray-400"
  const navDivider = dm ? "bg-white/10"  : "bg-gray-400"
  const navHover   = dm ? "hover:bg-white/[0.08]" : "hover:bg-[#d4e5b8]"
  const navLabel   = dm ? "text-gray-300" : "text-gray-700"
  const statCompletedCls = dm ? "bg-slate-800/60 border-slate-700/50" : "bg-slate-50 border-slate-100"
  const statOnTrackCls = dm
    ? "bg-emerald-900/40 border-emerald-700/60 hover:border-emerald-500 hover:-translate-y-0.5 active:translate-y-0 transition-all duration-150 cursor-pointer"
    : "bg-emerald-50 border-emerald-300 hover:border-emerald-400 hover:-translate-y-0.5 active:translate-y-0 transition-all duration-150 cursor-pointer"
  const statAtRiskCls = dm
    ? "bg-amber-900/40 border-amber-700/60 hover:border-amber-500 hover:-translate-y-0.5 active:translate-y-0 transition-all duration-150 cursor-pointer"
    : "bg-amber-50 border-amber-300 hover:border-amber-400 hover:-translate-y-0.5 active:translate-y-0 transition-all duration-150 cursor-pointer"
  const statCriticalCls = dm
    ? "bg-rose-900/40 border-rose-700/60 hover:border-rose-500 hover:-translate-y-0.5 active:translate-y-0 transition-all duration-150 cursor-pointer"
    : "bg-rose-50 border-rose-300 hover:border-rose-400 hover:-translate-y-0.5 active:translate-y-0 transition-all duration-150 cursor-pointer"
  const statOnTrackGlow = dm
    ? { boxShadow: '0 0 14px 2px rgba(16,185,129,0.30), inset 0 0 0 1px rgba(16,185,129,0.20)' }
    : { boxShadow: '0 0 12px 2px rgba(16,185,129,0.22), inset 0 0 0 1px rgba(16,185,129,0.15)' }
  const statAtRiskGlow = dm
    ? { boxShadow: '0 0 14px 2px rgba(245,158,11,0.28), inset 0 0 0 1px rgba(245,158,11,0.18)' }
    : { boxShadow: '0 0 12px 2px rgba(245,158,11,0.22), inset 0 0 0 1px rgba(245,158,11,0.15)' }
  const statCriticalGlow = dm
    ? { boxShadow: '0 0 14px 2px rgba(244,63,94,0.30), inset 0 0 0 1px rgba(244,63,94,0.20)' }
    : { boxShadow: '0 0 12px 2px rgba(244,63,94,0.22), inset 0 0 0 1px rgba(244,63,94,0.15)' }

  return (
    <div className={`h-screen ${shellBg} flex overflow-hidden`} style={{ opacity: themeReady ? 1 : 0, transition: 'opacity 0.15s ease' }}>
      {/* Left Sidebar */}
      <aside
        className={`w-20 flex flex-col items-center pt-4 pb-3`}
        style={dm ? {
          background: '#0c1422',
          boxShadow: '1px 0 12px rgba(0,0,0,0.4)',
        } : {
          background: '#d1d5db',
        }}
      >
        {/* Logo */}
        <button className="w-8 h-8 mb-4 cursor-pointer" onClick={() => setActiveNav("PM Board")} title="Go to PM Board">
          <Image
            src={dm ? "/logos/logo-white.svg" : "/logos/logo-black.svg"}
            alt="ProMarshal"
            width={32}
            height={32}
            className="w-full h-full object-contain"
          />
        </button>

        {/* Divider - aligned with top bar bottom */}
        <div className={`w-12 h-0.5 ${navDivider} mb-6`}></div>

        {/* Navigation Items - Better spacing */}
        <nav className="flex flex-col space-y-4 w-full px-2">
          {navigationItems.map((item) => (
            <button
              key={item.label}
              onClick={() => setActiveNav(item.label)}
              className={`flex flex-col items-center justify-center px-2 py-3 rounded-xl transition ${activeNav === item.label
                ? "bg-[#78a530]"
                : navHover
                }`}
            >
              <div className="w-8 h-8 mb-1 flex items-center justify-center">
                {item.svg ? (
                  <span className={activeNav === item.label ? "text-white" : dm ? "text-gray-300" : "text-gray-500"}>
                    {item.svg}
                  </span>
                ) : (
                  <Image
                    src={item.icon!}
                    alt={item.label}
                    width={32}
                    height={32}
                    className="w-8 h-8 object-contain"
                    style={(dm || activeNav === item.label) ? { filter: 'brightness(0) invert(1)' } : undefined}
                  />
                )}
              </div>
              <span className={`text-xs text-center leading-tight font-bold w-full ${activeNav === item.label ? "text-white" : navLabel
                }`}>
                {item.label}
              </span>
            </button>
          ))}
        </nav>
      </aside>

      {/* Main Content */}
      <div className="flex-1 flex flex-col">
        {/* Top Header */}
        <header
          className="h-16 flex items-center justify-between px-6"
          style={dm ? {
            background: '#0c1422',
            boxShadow: '0 1px 16px rgba(0,0,0,0.35)',
          } : {
            background: '#d1d5db',
          }}
        >
          {/* Project Selector */}
          <div className="flex items-center relative">
            {sortedProjects.length > 0 ? (
              <div className="relative">
                <button
                  onClick={() => setShowProjectDropdown(!showProjectDropdown)}
                  className={`min-w-[200px] max-w-[300px] rounded-lg px-3 py-1.5 pr-6 text-sm cursor-pointer focus:outline-none focus:ring-2 focus:ring-[#78a530] text-left flex items-center justify-between shadow-sm border ${dm ? "bg-[#1e2638] border-white/[0.12] text-gray-100" : "bg-white border-gray-200 text-gray-800"}`}
                >
                  <span className="truncate">{selectedProject?.project_name || "Select project"}</span>
                  <svg className={`w-4 h-4 ml-2 flex-shrink-0 ${dm ? "text-gray-400" : "text-gray-500"}`} fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
                  </svg>
                </button>

                {showProjectDropdown && (
                  <>
                    {/* Backdrop to close dropdown when clicking outside */}
                    <div
                      className="fixed inset-0 z-[55]"
                      onClick={() => setShowProjectDropdown(false)}
                    />

                    {/* Dropdown Menu */}
                    <div className={`absolute left-0 top-full mt-1 min-w-[220px] max-w-[300px] rounded-xl shadow-xl z-[60] max-h-80 overflow-y-auto border ${dm ? "bg-[#1e2638] border-white/[0.10]" : "bg-white border-gray-300"}`}>
                      {sortedProjects.map((project) => (
                        <button
                          key={project._id}
                          onClick={() => handleProjectChange(project._id)}
                          className={`w-full px-3 py-2.5 text-left text-sm transition ${selectedProject?._id === project._id
                            ? dm ? "bg-white/[0.08] text-gray-100 font-semibold" : "bg-gray-50 text-gray-900 font-semibold"
                            : dm ? "text-gray-300 hover:bg-white/[0.06]" : "text-gray-700 hover:bg-gray-50"}`}
                        >
                          <span className="truncate block">{project.project_name}</span>
                        </button>
                      ))}

                      {/* Divider */}
                      <div className={`border-t my-1 ${dm ? "border-white/[0.08]" : "border-gray-200"}`}></div>

                      {/* New Project Space Option */}
                      <button
                        onClick={() => handleProjectChange("create-new")}
                        className={`w-full px-3 py-2.5 text-left text-sm font-semibold text-[#78a530] transition ${dm ? "hover:bg-white/[0.06]" : "hover:bg-gray-50"}`}
                      >
                        + New Project Space
                      </button>
                    </div>
                  </>
                )}
              </div>
            ) : (
              <div className="text-slate-600 text-sm">
                No projects yet. <button onClick={() => window.location.href = "/project-setup"} className="text-[#78a530] hover:underline">Create a Project Space</button>
              </div>
            )}
          </div>

          {/* Right Side - Dark Mode Toggle + Avatar Menu */}
          <div className="flex items-center gap-3">
            {/* Dark / Light mode toggle */}
            <button
              onClick={() => { const next = !isDarkMode; setIsDarkMode(next); localStorage.setItem("promarshal_dark_mode", String(next)) }}
              className={`w-9 h-9 flex items-center justify-center rounded-xl border transition ${
                isDarkMode
                  ? "bg-[#1e2638] border-white/10 text-gray-300 hover:border-white/20 hover:text-white"
                  : "bg-white border-gray-200 text-gray-500 hover:border-[#78a530] hover:text-[#78a530]"
              } shadow-sm`}
              title={isDarkMode ? "Switch to light mode" : "Switch to dark mode"}
              aria-label={isDarkMode ? "Switch to light mode" : "Switch to dark mode"}
            >
              {isDarkMode ? (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m6.364.386-1.591 1.591M21 12h-2.25m-.386 6.364-1.591-1.591M12 18.75V21m-4.773-4.227-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z" />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.72 9.72 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z" />
                </svg>
              )}
            </button>
            <div className="relative">
              <button
                onClick={() => setShowUserMenu(!showUserMenu)}
                className="w-8 h-8 flex items-center justify-center rounded-full bg-[#78a530] text-white text-sm font-bold ring-2 ring-[#78a530]/30"
              >
                {userName?.charAt(0)?.toUpperCase() || "U"}
              </button>
              {showUserMenu && (
                <>
                  {/* Backdrop to close menu when clicking outside */}
                  <div
                    className="fixed inset-0 z-40"
                    onClick={() => setShowUserMenu(false)}
                  />
                  <div className={`absolute right-0 top-10 w-56 rounded-xl shadow-lg z-50 border ${dm ? "bg-[#0e1521] border-white/[0.08]" : "bg-white border-gray-200"}`}>
                    {/* User Info */}
                    <div className={`px-4 py-3 border-b ${dm ? "border-white/[0.08]" : "border-gray-100"}`}>
                      <p className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>{userName}</p>
                      <p className={`text-xs truncate mt-0.5 ${dm ? "text-gray-400" : "text-gray-500"}`}>{userId}</p>
                    </div>
                    {/* Sign Out */}
                    <button
                      onClick={() => signOut({ callbackUrl: "/signup" })}
                      className={`w-full px-4 py-2.5 text-left text-sm rounded-b-xl transition ${dm ? "text-gray-300 hover:bg-white/[0.06]" : "text-gray-700 hover:bg-gray-50"}`}
                    >
                      Sign out
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </header>

        {/* Content Area */}
        <main className={`flex-1 ${mainBg} pt-5 px-6 pb-6 rounded-tl-[20px] relative ${activeNav === "Forecast" || activeNav === "Project Brain" || activeNav === "Pulse"
          ? "overflow-hidden flex flex-col"
          : "overflow-auto"
          }`}>
          {/* Ambient background glows — fixed to the main panel */}
          {dm && (
            <div className="pointer-events-none absolute inset-0 overflow-hidden" style={{ zIndex: 0 }}>
              {/* Top-left green orb */}
              <div style={{ position: 'absolute', top: '-120px', left: '-80px', width: '500px', height: '500px', borderRadius: '50%', background: 'radial-gradient(circle, rgba(120,165,48,0.07) 0%, transparent 70%)', filter: 'blur(40px)' }} />
              {/* Bottom-right blue orb */}
              <div style={{ position: 'absolute', bottom: '-100px', right: '-60px', width: '460px', height: '460px', borderRadius: '50%', background: 'radial-gradient(circle, rgba(99,130,220,0.06) 0%, transparent 70%)', filter: 'blur(50px)' }} />
              {/* Center subtle pulse */}
              <div style={{ position: 'absolute', top: '40%', left: '50%', transform: 'translate(-50%,-50%)', width: '600px', height: '300px', borderRadius: '50%', background: 'radial-gradient(ellipse, rgba(120,165,48,0.035) 0%, transparent 70%)', filter: 'blur(60px)' }} />
            </div>
          )}
          {/* PM Board Content */}
          {activeNav === "PM Board" && (
            <div className="h-full flex flex-col relative" style={{ zIndex: 1 }}>
              {/* Header: Greeting */}
              <div className="mb-4 flex-shrink-0">
                <div className="flex items-center justify-between">
                  <div>
                    <h1 className={`text-xl font-bold ${textPrimary} tracking-tight`}>
                      Hey {firstName}
                    </h1>
                    <p className={`text-sm ${textSecondary} mt-0.5`}>
                      Here&apos;s your project overview
                    </p>
                  </div>

                  <div className="ml-auto flex items-center gap-3">
                    {/* Sprint Info - Always visible, elegant inline design */}
                    {activeSprint && (
                      <div
                        className={`
                          px-3 py-2 rounded-xl ${dm ? 'bg-[#1e2638] border-white/[0.07]' : 'bg-white border-gray-200'} border shadow-sm
                          border-l-4
                          ${projectHealth.critical > 0
                            ? 'border-l-red-500'
                            : projectHealth.atRisk > projectHealth.onTrack
                              ? 'border-l-amber-500'
                              : 'border-l-emerald-500'
                          }
                        `}
                      >
                        <div className="flex items-center gap-2">
                          {/* Sprint icon */}
                          <div className={`
                            p-1.5 rounded-lg
                            ${projectHealth.critical > 0
                              ? 'bg-red-50'
                              : projectHealth.atRisk > projectHealth.onTrack
                                ? 'bg-amber-50'
                                : 'bg-green-50'
                            }
                          `}>
                            <svg className={`
                              w-4 h-4
                              ${projectHealth.critical > 0
                                ? (dm ? 'text-red-400' : 'text-red-600')
                                : projectHealth.atRisk > projectHealth.onTrack
                                  ? (dm ? 'text-amber-400' : 'text-amber-600')
                                  : (dm ? 'text-green-400' : 'text-green-600')
                              }
                            `} fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                            </svg>
                          </div>

                          {/* Sprint details */}
                          <div className="text-left">
                            <div className="flex items-center gap-1.5">
                              <span className={`text-xs font-medium ${textSecondary} uppercase tracking-wide`}>Active Sprint</span>
                            </div>
                            <div className="flex items-center gap-2 mt-0.5">
                              <span className={`text-sm font-semibold ${textPrimary}`}>{activeSprint.name}</span>
                              <span className={`text-xs ${textMuted}`}>•</span>
                              <span className={`text-xs ${textSecondary}`}>
                                {new Date(activeSprint.startDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                                {' - '}
                                {new Date(activeSprint.endDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    <button
                      onClick={() => triggerPmBoardRefresh(true)}
                      className={`flex items-center justify-center w-9 h-9 ${dm ? 'bg-[#1e2638] border-white/10 text-gray-400' : 'bg-white border-gray-200 text-gray-400'} rounded-xl border hover:border-[#78a530] hover:text-[#78a530] transition disabled:opacity-50 shadow-sm`}
                      disabled={pmBoardRefreshing}
                      title={pmBoardRefreshing ? "Refreshing..." : "Refresh"}
                      aria-label={pmBoardRefreshing ? "Refreshing..." : "Refresh"}
                    >
                      <svg
                        className={`w-4 h-4 ${pmBoardRefreshing ? "animate-spin" : ""}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        strokeWidth={2}
                        stroke="currentColor"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992V4.356m-2.229 11.29a9 9 0 11-2.229-9.304" />
                      </svg>
                    </button>
                    {showPmBoardBackgroundRefreshing && (
                      <span className={`text-xs ${textMuted}`}>Updating insights...</span>
                    )}

                    {/* Collapsed Quick Actions - Right (shown when collapsed) */}
                    {!isQuickActionsExpanded && (
                      <div className={`flex items-center gap-2 ${dm ? 'bg-[#1e2638] border-white/10' : 'bg-white border-gray-200'} rounded-xl px-3 py-2 border shadow-sm`}>
                        <span className={`text-[11px] font-semibold ${textMuted} uppercase tracking-wide mr-1`}>Quick:</span>
                        {setupCards.map((card) => (
                          <button
                            key={card.number}
                            onClick={() => {
                            if (card.number === 1) {
                                openControlPanelTab("Manage Team")
                              } else if (card.number === 2) {
                                openControlPanelTab("Integrate Tools")
                              } else if (card.number === 3) {
                                openControlPanelTab("Project Settings")
                              }
                            }}
                            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg hover:bg-white hover:shadow-sm border border-transparent hover:border-gray-200 transition"
                            title={card.label}
                          >
                            <div className="w-5 h-5 bg-white rounded flex items-center justify-center shadow-sm border border-gray-100">
                              <Image
                                src={card.icon}
                                alt={card.label}
                                width={12}
                                height={12}
                                className="object-contain"
                              />
                            </div>
                            <span className="text-sm font-medium text-gray-600 hidden xl:inline">{card.label.split(' ')[0]}</span>
                          </button>
                        ))}
                        <button
                          onClick={() => setIsQuickActionsExpanded(true)}
                          className="ml-2 p-1 hover:bg-gray-200 rounded transition"
                          title="Expand guide"
                        >
                          <svg className="w-4 h-4 text-gray-600" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                          </svg>
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Expanded Quick Actions Guide */}
              {isQuickActionsExpanded && (
                <div className="mb-6 flex-shrink-0">
                  <div className={`${cardBg} border ${cardBorder} rounded-2xl p-4 max-w-4xl mx-auto shadow-sm`}>
                    {/* Header with Toggle */}
                    <div className="flex items-center justify-between mb-4">
                      <h2 className={`text-lg font-semibold ${textPrimary}`}>Quick Start Guide</h2>
                      <button
                        onClick={() => setIsQuickActionsExpanded(false)}
                        className="p-1.5 hover:bg-gray-200 rounded-lg transition"
                        title="Collapse guide"
                      >
                        <svg className="w-5 h-5 text-gray-600" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
                        </svg>
                      </button>
                    </div>

                    {/* Action Cards Grid */}
                    <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-3 gap-3">
                      {setupCards.map((card) => (
                        <button
                          key={card.number}
                          onClick={() => {
                            if (card.number === 1) {
                              openControlPanelTab("Manage Team")
                            } else if (card.number === 2) {
                              openControlPanelTab("Integrate Tools")
                            } else if (card.number === 3) {
                              openControlPanelTab("Project Settings")
                            }
                          }}
                          className={`${dm ? 'bg-[#192035] border-white/[0.07] hover:border-[#78a530] hover:bg-[#1e2a40]' : 'bg-[#f8fafc] border-gray-200 hover:border-[#78a530] hover:bg-white'} border rounded-xl p-3 hover:shadow-md transition-all group`}
                        >
                          <div className="flex flex-col items-center text-center">
                            {/* Step Number */}
                            <div className="w-6 h-6 bg-[#78a530] text-white rounded-full flex items-center justify-center text-sm font-bold mb-2">
                              {card.number}
                            </div>

                            {/* Icon */}
                            <div className={`w-12 h-12 ${dm ? 'bg-white/[0.06]' : 'bg-gray-50'} rounded-lg flex items-center justify-center mb-2 group-hover:bg-[#78a530]/10 transition`}>
                              <Image
                                src={card.icon}
                                alt={card.label}
                                width={24}
                                height={24}
                                className="object-contain"
                                style={dm ? { filter: 'brightness(0) invert(1)' } : undefined}
                              />
                            </div>

                            {/* Label */}
                            <p className={`text-sm font-semibold ${dm ? 'text-gray-300' : 'text-gray-900'} group-hover:text-[#78a530] transition`}>
                              {card.label}
                            </p>
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Active Sprint Notification - Slides in from left under greeting */}
              {activeSprint && showSprintNotification && (
                <div className="mb-4 flex-shrink-0 slide-in-left">
                  <div
                    className={`
                      relative overflow-hidden
                      bg-white rounded-lg
                      px-4 py-2.5
                      border-2
                      shadow-md
                      max-w-md
                      ${projectHealth.critical > 0
                        ? 'border-red-500 animate-pulse-border-red'
                        : projectHealth.atRisk > projectHealth.onTrack
                          ? 'border-amber-500 animate-pulse-border-amber'
                          : 'border-green-500 animate-pulse-border-green'
                      }
                    `}
                  >
                    <div className="relative z-10 flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2">
                        {/* Sprint Icon */}
                        <svg className="w-4 h-4 text-gray-600 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                        </svg>

                        {/* Sprint Info */}
                        <p className="text-sm font-semibold text-gray-800">
                          {activeSprint.name}
                          <span className="mx-1.5 text-gray-400">•</span>
                          <span className="text-gray-600 font-normal text-xs">
                            {new Date(activeSprint.startDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                            {' - '}
                            {new Date(activeSprint.endDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                          </span>
                        </p>
                      </div>

                      {/* Close Button */}
                      <button
                        onClick={() => setShowSprintNotification(false)}
                        className="flex-shrink-0 p-1 hover:bg-gray-100 rounded transition"
                        title="Dismiss"
                      >
                        <svg className="w-4 h-4 text-gray-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    </div>

                    {/* Animated border gradient */}
                    <div
                      className={`
                        absolute inset-0 rounded-lg opacity-30
                        ${projectHealth.critical > 0
                          ? 'bg-gradient-to-r from-red-500 via-red-300 to-red-500 animate-border-flow'
                          : projectHealth.atRisk > projectHealth.onTrack
                            ? 'bg-gradient-to-r from-amber-500 via-amber-300 to-amber-500 animate-border-flow'
                            : 'bg-gradient-to-r from-green-500 via-green-300 to-green-500 animate-border-flow'
                        }
                      `}
                      style={{ filter: 'blur(8px)' }}
                    />
                  </div>
                </div>
              )}

              {showPmBoardInitialLoading && (
                <div className={`fixed left-20 right-0 top-16 bottom-0 z-50 ${dm ? 'bg-[#111520]/80' : 'bg-[#f5f7fb]/80'} backdrop-blur-sm flex items-center justify-center pointer-events-none`}>
                  <div className="flex flex-col items-center gap-1 -translate-y-20">
                    <img
                      src="/logos/loading.gif"
                      alt="Loading insights"
                      className="w-72 h-72 object-contain -mb-8"
                      style={dm ? { filter: 'brightness(0) invert(1)' } : undefined}
                    />
                    <div className="h-10 overflow-hidden text-center">
                      <div
                        className="transition-transform duration-500 ease-out"
                        style={{ transform: `translateY(-${pmInitialLoadingMessageIndex * 40}px)` }}
                      >
                        {pmInitialLoadingMessages.map((message) => (
                          <p key={message} className={`h-10 text-2xl font-semibold leading-10 ${dm ? 'text-white' : 'text-gray-800'}`}>
                            {message}
                          </p>
                        ))}
                      </div>
                    </div>
                    <p className={`text-base tracking-wide ${dm ? 'text-white/60' : 'text-gray-500'}`}>AI agents are processing your project context...</p>
                  </div>
                </div>
              )}

              {/* Side-by-Side: Project Health + Tasks */}
              {selectedProject && shouldShowWorkItemWidgets && !showPmBoardInitialLoading && (
                <div className="mb-6 flex-shrink-0 grid grid-cols-1 xl:grid-cols-2 gap-4 items-stretch">
                  {/* Left: Project Health (Adaptive) */}
                  <div className={`${cardBg} rounded-2xl overflow-hidden h-full relative`} style={cardStyle}>
                    {/* Content Section */}
                    <div className="px-4 pt-5 pb-4">
                      <p className={`text-sm font-bold uppercase tracking-widest ${dm ? 'text-gray-100' : 'text-gray-800'} text-center mb-3`}>Project Health</p>
                      {showPmBoardInitialLoading ? (
                        <div className="py-3">
                          <div className="flex items-center gap-2">
                            <span className="inline-block w-3.5 h-3.5 border-2 border-gray-300 border-t-[#78a530] rounded-full animate-spin" />
                            <p className="text-xs text-gray-500">Refreshing project health...</p>
                          </div>
                        </div>
                      ) : healthHierarchy ? (
                        <>
                          {/* Circle Progress Ring */}
                          {(() => {
                            const total = Math.max(1, pmWorkItemCompletion.total)
                            const completed = animatedHealth.completed
                            const fillPct = Math.min(completed / total, 1)
                            const size = 160
                            const strokeWidth = 10
                            const radius = (size - strokeWidth) / 2
                            const circumference = 2 * Math.PI * radius
                            const strokeDashoffset = circumference * (1 - fillPct)
                            const color = fillPct < 0.5 ? '#ef4444' : fillPct < 0.8 ? '#f59e0b' : '#10b981'
                            return (
                              <div className="flex justify-center mb-5">
                                <div className="relative inline-flex items-center justify-center">
                                  {/* SVG ring */}
                                  <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
                                    <circle cx={size/2} cy={size/2} r={radius} fill="transparent"
                                      stroke={dm ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.12)'}
                                      strokeWidth={strokeWidth} />
                                    <circle cx={size/2} cy={size/2} r={radius} fill="transparent"
                                      stroke={color} strokeWidth={strokeWidth}
                                      strokeDasharray={circumference} strokeDashoffset={strokeDashoffset}
                                      transform={`rotate(-90 ${size/2} ${size/2})`} strokeLinecap="round"
                                      style={{ transition: 'stroke-dashoffset 0.05s linear' }} />
                                  </svg>
                                  {/* Center value */}
                                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                                    <span className="text-5xl font-black tabular-nums" style={{ color }}>{completed}</span>
                                    <span className={`text-sm font-semibold mt-1 ${dm ? 'text-gray-300' : 'text-gray-600'}`}>of {total}</span>
                                  </div>
                                </div>
                              </div>
                            )
                          })()}
                          {/* 3 status cards */}
                          <div className="grid grid-cols-3 gap-3">
                            {([
                              { key: 'on_track' as const, label: 'On Track', count: animatedHealth.onTrack, icon: CheckCircle2, color: '#10b981', delay: 0 },
                              { key: 'at_risk' as const, label: 'At Risk', count: animatedHealth.atRisk, icon: AlertTriangle, color: '#f59e0b', delay: 150 },
                              { key: 'critical' as const, label: 'Critical', count: animatedHealth.critical, icon: XCircle, color: '#ef4444', delay: 300 },
                            ]).map(({ key, label, count, icon: Icon, color, delay }) => (
                              <button key={key} type="button"
                                onClick={() => openPulseProjectHealthByStatus(key)}
                                className={`rounded-xl border p-3 flex flex-col items-center gap-2 cursor-pointer transition hover:opacity-80 ${dm ? "border-white/[0.08]" : "border-gray-200 bg-white"}`}
                                style={dm ? { background: '#111828' } : {}}>
                                <div className="p-2 rounded-lg" style={{ backgroundColor: `${color}18`, border: `1px solid ${color}35` }}>
                                  <Icon className="w-5 h-5" style={{ color }} />
                                </div>
                                <span className="text-4xl font-black tabular-nums" style={{ color }}>{count}</span>
                                <span className={`text-xs font-bold uppercase tracking-wider ${dm ? 'text-gray-300' : 'text-gray-700'}`}>{label}</span>
                              </button>
                            ))}
                          </div>
                          {false && (
                          <>
                          <div className="grid items-center mb-3 pb-2 border-b border-gray-100 px-2 gap-2" style={{ gridTemplateColumns: 'minmax(0,48%) 110px minmax(0,1fr)' }}>
                            <span className="text-sm font-semibold text-gray-900">
                              {healthMetrics.label}
                            </span>
                            <div className="flex justify-center">
                              <Tooltip content={(() => {
                                const summary = healthHierarchy.summary || {}
                                const completedEpics = summary.completed_epics || 0
                                const totalEpics = summary.total_epics || 0
                                const completedSprints = summary.completed_sprints || 0
                                const totalSprints = summary.total_sprints || 0
                                const totalTasks = summary.total_tasks || 0
                                const onTrack = summary.on_track || 0

                                if (healthHierarchy.epics) {
                                  return `${completedEpics}/${totalEpics} completed`
                                } else if (healthHierarchy.sprints) {
                                  return `${completedSprints}/${totalSprints} completed`
                                } else if (healthHierarchy.tasks) {
                                  return `${onTrack}/${totalTasks} on track`
                                }
                                return '0% Complete'
                              })()}>
                                <span className="text-sm font-semibold text-gray-900 cursor-default">
                                  Status
                                </span>
                              </Tooltip>
                            </div>
                            <div className="flex justify-start">
                              <span className="text-sm font-semibold text-gray-900">Task Action</span>
                            </div>
                          </div>

                          {/* Hierarchical List */}
                          <div className="max-h-[500px] overflow-y-auto space-y-2">
                            {(() => {
                              const allItems = healthHierarchy.epics || healthHierarchy.sprints || healthHierarchy.tasks || []
                              const itemType = healthHierarchy.epics ? 'epic' : healthHierarchy.sprints ? 'sprint' : 'task'

                              const renderItems = (items: any[], keyPrefix = '') => items.map((item: any, index: number) => {
                                const itemId = `${keyPrefix}${itemType}-${index}`
                                const isExpanded = expandedItems.has(itemId)
                                const itemCompleted = isCompletedStatus(item.status)
                                const children = item.stories || item.tasks || []
                                const hasChildren = children.length > 0

                                // Get icon component based on item type
                                const getIssueIcon = () => {
                                  if (itemType === 'sprint') return renderIssueTypeIcon("sprint", "Sprint")
                                  return renderIssueTypeIcon(String(item.issue_type || itemType || "task"), String(item.issue_type || itemType || "Task"))
                                }

                                const healthTooltip = itemCompleted
                                  ? 'Completed'
                                  : (item.health_reason || (item.health_status === 'on_track' ? 'On track' : item.health_status === 'at_risk' ? 'At risk' : 'Critical'))

                                return (
                                  <div key={itemId} className={`rounded-lg border ${dm ? "border-white/[0.10] bg-[#1e2638]" : "border-gray-200 bg-white"}`}>
                                    {/* Parent Item Row — 3-col grid */}
                                    <div
                                      className={`grid items-center p-2.5 gap-2 ${hasChildren ? `cursor-pointer ${dm ? "hover:bg-white/[0.04]" : "hover:bg-gray-50"}` : ''}`}
                                      style={{ gridTemplateColumns: 'minmax(0,48%) 110px minmax(0,1fr)' }}
                                      onClick={() => {
                                        if (hasChildren) {
                                          setExpandedItems(prev => {
                                            const newSet = new Set(prev)
                                            if (newSet.has(itemId)) {
                                              newSet.delete(itemId)
                                            } else {
                                              newSet.add(itemId)
                                            }
                                            return newSet
                                          })
                                        } else if (item.task_key) {
                                          const rawType = String(item.issue_type || "task").toLowerCase()
                                          const focusType = rawType === "story" ? "story" : "task"
                                          openPulseProjectHealthFocus({
                                            focusType,
                                            focusKey: item.task_key,
                                            focusSeverity: item.health_status,
                                          })
                                        }
                                      }}
                                    >
                                      {/* Col 1: Name */}
                                      <div className="flex items-center gap-2 min-w-0">
                                        {hasChildren && (
                                          <svg
                                            className={`w-4 h-4 text-gray-400 transition-transform flex-shrink-0 ${isExpanded ? 'rotate-90' : ''}`}
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                          >
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                                          </svg>
                                        )}
                                        {!hasChildren && <span className="w-4 flex-shrink-0" />}
                                        <span
                                          className="inline-flex items-center w-4 h-4 flex-shrink-0"
                                          title={String(item.issue_type || itemType || "Task")}
                                        >
                                          {getIssueIcon()}
                                        </span>
                                        <Tooltip content={item.title}>
                                          <span className="text-xs text-gray-700 font-medium truncate leading-none cursor-default">
                                            {item.title}
                                          </span>
                                        </Tooltip>
                                      </div>
                                      {/* Col 2: Status badge */}
                                      <div className="flex justify-center">
                                        <Tooltip content={healthTooltip}>
                                          <div
                                            className={`w-[82px] h-[22px] inline-flex items-center justify-center rounded-full text-xs font-semibold flex-shrink-0 cursor-default ${itemCompleted
                                              ? 'bg-gray-100 text-gray-700'
                                              : item.health_status === 'on_track'
                                              ? 'bg-green-100 text-green-800'
                                              : item.health_status === 'at_risk'
                                                ? 'bg-amber-100 text-amber-800'
                                                : 'bg-rose-100 text-rose-800'
                                              }`}
                                          >
                                            {itemCompleted ? 'Completed' : item.health_status === 'on_track' ? 'On Track' :
                                              item.health_status === 'at_risk' ? 'At Risk' : 'Critical'}
                                          </div>
                                        </Tooltip>
                                      </div>
                                      {/* Col 3: Action */}
                                      <div className="flex justify-start min-w-0">
                                        {!hasChildren && !itemCompleted ? (
                                          <Tooltip content={item.health_action || 'Needs update'}>
                                            <span className="text-xs text-gray-500 truncate leading-tight cursor-default">
                                              {item.health_action || 'Needs update'}
                                            </span>
                                          </Tooltip>
                                        ) : null}
                                      </div>
                                    </div>

                                    {/* Nested Children (Stories/Tasks) */}
                                    {isExpanded && hasChildren && (
                                      <div className={`pb-2 space-y-1.5 ${dm ? "bg-white/[0.03]" : "bg-gray-100"}`}>
                                        {children.map((child: any, childIndex: number) => {
                                          const childCompleted = isCompletedStatus(child.status)
                                          const childHealthTooltip = childCompleted
                                            ? 'Completed'
                                            : (child.health_reason || (child.health_status === 'on_track' ? 'On track' : child.health_status === 'at_risk' ? 'At risk' : 'Critical'))
                                          const hasSubtasks = child.subtasks && child.subtasks.length > 0
                                          const taskId = `task-${itemId}-${childIndex}`
                                          const isTaskExpanded = expandedItems.has(taskId)

                                          // Get child icon based on issue_type
                                          const getChildIcon = () => {
                                            return renderIssueTypeIcon(String(child.issue_type || "task"), String(child.issue_type || "Task"))
                                          }

                                          return (
                                            <div key={childIndex} className={`rounded border ${dm ? "bg-[#1e2638] border-white/[0.08]" : "bg-white border-gray-200"}`}>
                                              {/* Child Row — same 3-col grid */}
                                              <div
                                                className={`grid items-center p-2.5 gap-2 ${hasSubtasks ? `cursor-pointer ${dm ? "hover:bg-white/[0.04]" : "hover:bg-gray-50"}` : ''}`}
                                                style={{ gridTemplateColumns: 'minmax(0,48%) 110px minmax(0,1fr)' }}
                                                onClick={() => {
                                                  if (hasSubtasks) {
                                                    setExpandedItems(prev => {
                                                      const newSet = new Set(prev)
                                                      if (newSet.has(taskId)) {
                                                        newSet.delete(taskId)
                                                      } else {
                                                        newSet.add(taskId)
                                                      }
                                                      return newSet
                                                    })
                                                  } else if (child.task_key) {
                                                    const childTypeRaw = String(child.issue_type || "task").toLowerCase()
                                                    const focusType = childTypeRaw === "story" ? "story" : "task"
                                                    openPulseProjectHealthFocus({
                                                      focusType,
                                                      focusKey: child.task_key,
                                                      focusSeverity: child.health_status,
                                                      focusParentKey: item.epic_key || item.task_key,
                                                    })
                                                  }
                                                }}
                                              >
                                                {/* Col 1: Name (indented for child level) */}
                                                <div className="flex items-center gap-2 min-w-0 pl-4">
                                                  {hasSubtasks && (
                                                    <svg
                                                      className={`w-3 h-3 text-gray-400 transition-transform flex-shrink-0 ${isTaskExpanded ? 'rotate-90' : ''}`}
                                                      fill="none"
                                                      stroke="currentColor"
                                                      viewBox="0 0 24 24"
                                                    >
                                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                                                    </svg>
                                                  )}
                                                  <span className="inline-flex items-center w-4 h-4 flex-shrink-0" title={child.issue_type || 'Task'}>
                                                    {getChildIcon()}
                                                  </span>
                                                  <Tooltip content={child.title}>
                                                    <span className="text-xs text-gray-600 truncate leading-none cursor-default">
                                                      {child.title}
                                                    </span>
                                                  </Tooltip>
                                                </div>
                                                {/* Col 2: Status badge */}
                                                <div className="flex justify-center">
                                                  <Tooltip content={childHealthTooltip}>
                                                    <div
                                                      className={`w-[82px] h-[22px] inline-flex items-center justify-center rounded-full text-xs font-semibold flex-shrink-0 cursor-default ${childCompleted
                                                        ? (dm ? 'bg-white/[0.08] text-gray-300' : 'bg-gray-100 text-gray-700')
                                                        : child.health_status === 'on_track'
                                                        ? (dm ? 'bg-green-900/40 text-green-300' : 'bg-green-100 text-green-800')
                                                        : child.health_status === 'at_risk'
                                                          ? (dm ? 'bg-amber-900/40 text-amber-300' : 'bg-amber-100 text-amber-800')
                                                          : (dm ? 'bg-rose-900/40 text-rose-300' : 'bg-rose-100 text-rose-800')
                                                        }`}
                                                    >
                                                      {childCompleted ? 'Completed' : child.health_status === 'on_track' ? 'On Track' :
                                                        child.health_status === 'at_risk' ? 'At Risk' : 'Critical'}
                                                    </div>
                                                  </Tooltip>
                                                </div>
                                                {/* Col 3: Action */}
                                                <div className="flex justify-start min-w-0">
                                                  {!hasSubtasks && !childCompleted ? (
                                                    <Tooltip content={child.health_action || 'Needs update'}>
                                                      <span className="text-xs text-gray-500 truncate leading-tight cursor-default">
                                                        {child.health_action || 'Needs update'}
                                                      </span>
                                                    </Tooltip>
                                                  ) : null}
                                                </div>
                                              </div>

                                              {/* Nested Subtasks */}
                                              {isTaskExpanded && hasSubtasks && (
                                                <div className={`px-3 pb-2 space-y-1 ${dm ? "bg-white/[0.03]" : "bg-gray-100"}`}>
                                                  {child.subtasks.map((subtask: any, subtaskIndex: number) => {
                                                    const subtaskHealthColor = subtask.health_status === 'on_track' ? 'bg-green-500' :
                                                      subtask.health_status === 'at_risk' ? 'bg-orange-500' : 'bg-red-500'
                                                    const subtaskCompleted = isCompletedStatus(subtask.status)
                                                    const subtaskHealthTooltip = subtaskCompleted
                                                      ? 'Completed'
                                                      : (subtask.health_reason || (subtask.health_status === 'on_track' ? 'On track' : subtask.health_status === 'at_risk' ? 'At risk' : 'Critical'))

                                                    return (
                                                      <div key={subtaskIndex} className={`flex items-center justify-between py-1 px-2 rounded border ${dm ? "bg-[#1e2638] border-white/[0.07]" : "bg-white border-gray-200"}`}>
                                                        <div className="flex items-center gap-2 flex-1 min-w-0">
                                                          <span className="inline-flex items-center w-3 h-3" title="Subtask">
                                                            {renderIssueTypeIcon("subtask", "Subtask")}
                                                          </span>
                                                          <span className="text-xs text-gray-500 truncate">
                                                            {subtask.title}
                                                          </span>
                                                        </div>
                                                        <Tooltip content={subtaskHealthTooltip}>
                                                          <div
                                                            className={`w-2 h-2 rounded-full ${subtaskHealthColor} flex-shrink-0 cursor-default`}
                                                          />
                                                        </Tooltip>
                                                      </div>
                                                    )
                                                  })}
                                                </div>
                                              )}
                                            </div>
                                          )
                                        })}
                                      </div>
                                    )}
                                  </div>
                                )
                              })

                              if (isFlatTaskMode && flatTaskGroups) {
                                const groupedSections = [
                                  {
                                    key: "attention",
                                    label: "Needs Attention",
                                    groups: [
                                      { key: "overdue", label: "Overdue", tone: "text-rose-700" },
                                      { key: "dueToday", label: "Due Today", tone: "text-amber-700" },
                                      { key: "dueSoon", label: "Due Soon (Next 3 Days)", tone: "text-amber-700" },
                                    ] as const,
                                  },
                                  {
                                    key: "upcoming",
                                    label: "Upcoming",
                                    groups: [
                                      { key: "dueThisWeek", label: "Later This Week", tone: "text-gray-600" },
                                      { key: "dueNextWeek", label: "Next Week", tone: "text-gray-600" },
                                      { key: "later", label: "Later", tone: "text-gray-600" },
                                      { key: "noDueDate", label: "No Due Date", tone: "text-gray-600" },
                                    ] as const,
                                  },
                                ]

                                return (
                                  <>
                                    {groupedSections.map((section) => {
                                      const sectionCount = section.groups.reduce((sum, group) => sum + flatTaskGroups![group.key].length, 0)
                                      if (sectionCount === 0) return null
                                      const isCollapsed = !!collapsedFlatSections[section.key]

                                      return (
                                        <div key={section.key} className="space-y-1.5">
                                          <button
                                            type="button"
                                            onClick={() => {
                                              setCollapsedFlatSections((prev) => ({
                                                ...prev,
                                                [section.key]: !prev[section.key],
                                              }))
                                            }}
                                            className="w-full flex items-center justify-between px-1 pt-1 text-left hover:bg-gray-50 rounded"
                                          >
                                            <span className="inline-flex items-center gap-2 text-xs font-medium text-gray-700">
                                              <svg
                                                className={`w-4 h-4 text-gray-400 transition-transform ${isCollapsed ? "" : "rotate-90"}`}
                                                fill="none"
                                                stroke="currentColor"
                                                viewBox="0 0 24 24"
                                              >
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                                              </svg>
                                              {section.label}
                                            </span>
                                            <span className="text-[11px] text-gray-400">{sectionCount}</span>
                                          </button>
                                          {!isCollapsed && section.groups.map((group) => {
                                            const groupItems = flatTaskGroups![group.key]
                                            if (!groupItems.length) return null

                                            return (
                                              <div key={group.key} className="space-y-1.5">
                                                <div className="flex items-center justify-between px-1">
                                                  <span className={`text-[11px] font-semibold ${group.tone}`}>{group.label}</span>
                                                  <span className="text-[11px] text-gray-400">{groupItems.length}</span>
                                                </div>
                                                {renderItems(groupItems, `flat-${group.key}-`)}
                                              </div>
                                            )
                                          })}
                                        </div>
                                      )
                                    })}

                                    {flatTaskGroups!.completed.length > 0 && (
                                      <div className="space-y-1.5">
                                        <button
                                          type="button"
                                          onClick={() => setShowFlatCompleted((prev) => !prev)}
                                          className="w-full flex items-center justify-between px-1 pt-1 text-left hover:bg-gray-50 rounded"
                                        >
                                          <span className="inline-flex items-center gap-2 text-xs font-medium text-gray-700">
                                            <svg
                                              className={`w-4 h-4 text-gray-400 transition-transform ${showFlatCompleted ? "rotate-90" : ""}`}
                                              fill="none"
                                              stroke="currentColor"
                                              viewBox="0 0 24 24"
                                            >
                                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                                            </svg>
                                            Completed
                                          </span>
                                          <span className="text-[11px] text-gray-400">{flatTaskGroups!.completed.length}</span>
                                        </button>
                                        {showFlatCompleted && (
                                          <div className="space-y-1.5">
                                            {renderItems(flatTaskGroups!.completed, "flat-completed-")}
                                          </div>
                                        )}
                                      </div>
                                    )}
                                  </>
                                )
                              }

                              return renderItems(allItems)
                            })()}
                          </div>
                          </>)}
                        </>
                      ) : (
                        <div className="py-3">
                          <p className="text-xs text-gray-500">No work item hierarchy available yet.</p>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Right: Tasks */}
                  <div className={`${cardBg} rounded-2xl overflow-hidden h-full`} style={cardStyle}>
                    <div className={`flex items-center justify-between px-5 py-4 border-b ${cardHeaderBorder}`} style={cardHeaderStyle}>
                      <h3 className={`text-base font-bold ${textPrimary}`}>Today&apos;s Focus <span className={`text-sm font-medium ${dm ? 'text-gray-400' : 'text-gray-500'}`}>({todayFocusTasks.length})</span></h3>
                      <button onClick={openPulseTodayFocus} className="text-sm font-medium text-[#78a530] hover:underline">
                        View Details
                      </button>
                    </div>
                    <div className="p-4 space-y-4">
                      {/* Due Today Section */}
                      <div>
                        {showPmBoardInitialLoading ? (
                          <p className="text-xs text-gray-500">Refreshing today&apos;s focus...</p>
                        ) : todayFocusTasks.length === 0 ? (
                          <p className="text-xs text-gray-400 italic py-1">No tasks due today</p>
                        ) : (
                          <div
                            className="space-y-2 overflow-y-auto pr-1"
                            style={{
                              maxHeight: '260px',
                              scrollbarWidth: 'thin',
                              scrollbarColor: 'transparent transparent'
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.scrollbarColor = 'rgba(148,163,184,0.4) transparent' }}
                            onMouseLeave={(e) => { e.currentTarget.style.scrollbarColor = 'transparent transparent' }}
                          >
                            {todayFocusTasks.map((task, index) => {
                              const isOverdue = task.highlight === "overdue"
                              const accentColor = isOverdue ? '#f43f5e' : '#f59e0b'
                              const cardColor = '#78a530'
                              const isHov = hoveredFocusTask === task.task_key
                              return (
                                <motion.div
                                  key={task.task_key}
                                  initial={{ opacity: 0, x: -30 }}
                                  animate={{ opacity: 1, x: 0 }}
                                  transition={{ duration: 0.35, delay: index * 0.1, ease: [0.4, 0, 0.2, 1] }}
                                  onHoverStart={() => setHoveredFocusTask(task.task_key)}
                                  onHoverEnd={() => setHoveredFocusTask(null)}
                                  whileHover={{ y: -2 }}
                                  className="relative overflow-hidden rounded-xl border cursor-pointer"
                                  style={{
                                    borderTopColor: dm ? (isHov ? `${cardColor}70` : `${cardColor}35`) : 'rgba(0,0,0,0.18)',
                                    borderRightColor: dm ? (isHov ? `${cardColor}70` : `${cardColor}35`) : 'rgba(0,0,0,0.18)',
                                    borderBottomColor: dm ? (isHov ? `${cardColor}70` : `${cardColor}35`) : 'rgba(0,0,0,0.18)',
                                    borderLeftColor: cardColor,
                                    borderLeftWidth: dm ? '2px' : '4px',
                                    background: dm ? `linear-gradient(135deg, ${cardColor}15, ${cardColor}06)` : (isHov ? `${cardColor}06` : 'white'),
                                    boxShadow: dm ? (isHov ? `0 4px 20px ${cardColor}25` : `0 1px 4px ${cardColor}10`) : (isHov ? `0 4px 16px ${cardColor}20, 0 1px 4px rgba(0,0,0,0.06)` : '0 1px 4px rgba(0,0,0,0.06)'),
                                    transition: 'border-color 0.2s, box-shadow 0.2s, background 0.2s',
                                  }}
                                >
                                  <div className="px-3 py-3 flex items-center gap-3">
                                    {/* PRM key */}
                                    <motion.span
                                      animate={{ scale: isHov ? 1.05 : 1 }}
                                      transition={{ duration: 0.2 }}
                                      className="text-xs font-mono font-bold shrink-0 w-16"
                                      style={{ color: dm ? '#94a3b8' : '#64748b' }}
                                    >{task.task_key}</motion.span>
                                    {/* Title */}
                                    <p className={`text-sm font-semibold ${dm ? 'text-gray-100' : 'text-gray-800'} flex-1 truncate`}>
                                      {task.title}
                                    </p>
                                    {/* Status badge */}
                                    <span className="text-xs px-2 py-0.5 rounded-full font-semibold shrink-0"
                                      style={{ background: `${accentColor}25`, color: accentColor, border: `1px solid ${accentColor}50` }}>
                                      {isOverdue ? "Overdue" : "Due Today"}
                                    </span>
                                    {/* Assignee */}
                                    <span className={`text-sm font-medium shrink-0 ${dm ? 'text-gray-300' : 'text-gray-600'} max-w-[100px] truncate ml-3`}>
                                      {task.assignee_name || "Unassigned"}
                                    </span>
                                  </div>
                                  {/* Hover bottom bar */}
                                  <motion.div
                                    className="absolute bottom-0 left-0 right-0 h-[2px]"
                                    style={{ background: `linear-gradient(90deg, ${cardColor}, ${cardColor}50)`, transformOrigin: 'left' }}
                                    initial={{ scaleX: 0 }}
                                    animate={{ scaleX: isHov ? 1 : 0 }}
                                    transition={{ duration: 0.25 }}
                                  />
                                </motion.div>
                              )
                            })}
                          </div>
                        )}
                      </div>

                    </div>
                  </div>
                </div>
              )}

              {/* Primary Widgets Row: Alerts + Action Inbox */}
              {!showPmBoardInitialLoading && (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6 items-start">

                {/* Widget 1: Critical Alerts & Recommendations */}
                <div className="xl:col-span-1">
                <ProjectFeedWidget
                  alerts={effectiveAlerts}
                  actionItemOpenCount={actionItemOpenCount}
                  isLoading={showPmBoardInitialLoading}
                  isDarkMode={isDarkMode}
                  onOpenWorkItems={openPulseWorkItemRecommendations}
                  onOpenBestPractice={openPulseBestPracticeRecommendations}
                  onOpenActionItems={openPulseActionItems}
                />
                </div>

                {/* Widget 2: Action Inbox */}
                <div className={`${cardBg} rounded-2xl flex flex-col overflow-hidden xl:col-span-1`} style={cardStyle}>
                  <div className={`px-5 py-4 border-b ${cardHeaderBorder} flex-shrink-0 flex justify-between items-center`} style={cardHeaderStyle}>
                    <h2 className={`text-base font-bold ${textPrimary}`}>Action Inbox</h2>
                    <button onClick={() => setShowReviewModal(true)} className="text-sm text-[#78a530] font-medium hover:underline">
                      View All
                    </button>
                  </div>
                  <div className="flex-1 overflow-y-auto p-4 space-y-2"
                    style={{ scrollbarWidth: 'thin', scrollbarColor: 'transparent transparent' }}
                    onMouseEnter={(e) => { e.currentTarget.style.scrollbarColor = 'rgba(148,163,184,0.4) transparent' }}
                    onMouseLeave={(e) => { e.currentTarget.style.scrollbarColor = 'transparent transparent' }}
                  >
                    {showPmBoardInitialLoading ? (
                      <p className={`text-sm ${textSecondary}`}>Refreshing actions...</p>
                    ) : inboxCounts.people + inboxCounts.setup + inboxCounts.governance === 0 ? (
                      <p className={`text-sm ${textSecondary} italic`}>No pending actions right now.</p>
                    ) : null}

                    {/* Pending Invites */}
                    {showPmBoardInitialLoading ? null : pendingInvites.map((invite, index) => {
                      const daysAgo = (dateStr: string | null) => {
                        if (!dateStr) return null
                        const d = Math.floor((Date.now() - new Date(dateStr).getTime()) / (24 * 60 * 60 * 1000))
                        return d === 0 ? 'today' : d === 1 ? '1 day ago' : `${d} days ago`
                      }
                      const invitedLabel = invite.invited_at ? `Sent ${daysAgo(invite.invited_at)}` : 'Invite sent'
                      const isExpired = invite.expires_at ? new Date(invite.expires_at) < new Date() : false
                      const accent = isExpired ? '#f43f5e' : '#f59e0b'
                      const itemKey = `invite-${invite.email}`
                      const isHov = hoveredInboxItem === itemKey
                      return (
                        <motion.div key={invite.email}
                          initial={{ opacity: 0, x: -30 }} animate={{ opacity: 1, x: 0 }}
                          transition={{ duration: 0.35, delay: index * 0.08, ease: [0.4, 0, 0.2, 1] }}
                          whileHover={{ y: -2 }}
                          onHoverStart={() => setHoveredInboxItem(itemKey)}
                          onHoverEnd={() => setHoveredInboxItem(null)}
                          className="relative overflow-hidden rounded-xl border-2"
                          style={{
                            borderTopColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderRightColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderBottomColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderLeftColor: accent,
                            borderLeftWidth: dm ? '2px' : '4px',
                            background: dm ? `linear-gradient(135deg, ${accent}15, ${accent}06)` : (isHov ? `${accent}08` : 'white'),
                            boxShadow: dm ? (isHov ? `0 4px 20px ${accent}25` : `0 1px 4px ${accent}08`) : (isHov ? `0 4px 16px ${accent}30, 0 1px 4px rgba(0,0,0,0.06)` : '0 1px 4px rgba(0,0,0,0.06)'),
                            transition: 'border-color 0.2s, box-shadow 0.2s, background 0.2s',
                          }}
                        >
                          <div className="p-3 flex items-center justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0"
                                style={{ backgroundColor: `${accent}25`, color: accent, border: `1px solid ${accent}50` }}>
                                {invite.name.charAt(0).toUpperCase()}
                              </div>
                              <div className="min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <p className={`text-sm font-semibold ${textPrimary} truncate`}>{invite.name}</p>
                                  <span className="text-xs px-1.5 py-0.5 rounded-full font-semibold"
                                    style={{ background: `${accent}20`, color: accent, border: `1px solid ${accent}40` }}>
                                    {isExpired ? 'Expired' : 'Pending'}
                                  </span>
                                </div>
                                <p className={`text-sm ${dm ? 'text-gray-400' : 'text-gray-500'} truncate`}>{invite.email}</p>
                                <p className="text-sm mt-1.5 font-medium" style={{ color: accent }}>{invitedLabel} · Yet to accept</p>
                              </div>
                            </div>
                            <div className="flex-shrink-0">
                              {remindSuccess === invite.email ? (
                                <span className="text-xs text-green-500 font-semibold">Sent!</span>
                              ) : (
                                <button onClick={() => handleRemindMember(invite.email)}
                                  className="text-xs px-3 py-1.5 rounded-lg font-semibold transition-all"
                                  style={{ background: `${accent}20`, color: accent, border: `1px solid ${accent}50` }}>
                                  {isExpired ? 'Resend' : 'Remind'}
                                </button>
                              )}
                            </div>
                          </div>
                          <motion.div className="absolute bottom-0 left-0 right-0 h-[2px]"
                            style={{ background: `linear-gradient(90deg, ${accent}, ${accent}50)`, transformOrigin: 'left' }}
                            initial={{ scaleX: 0 }} animate={{ scaleX: isHov ? 1 : 0 }} transition={{ duration: 0.25 }} />
                        </motion.div>
                      )
                    })}

                    {/* Orphan Assignees */}
                    {showPmBoardInitialLoading ? null : orphanAssignees.map((orphan, index) => {
                      const accent = '#60a5fa'
                      const itemKey = `orphan-${orphan.account_id}`
                      const isHov = hoveredInboxItem === itemKey
                      return (
                        <motion.div key={orphan.account_id}
                          initial={{ opacity: 0, x: -30 }} animate={{ opacity: 1, x: 0 }}
                          transition={{ duration: 0.35, delay: (pendingInvites.length + index) * 0.08, ease: [0.4, 0, 0.2, 1] }}
                          whileHover={{ y: -2 }}
                          onHoverStart={() => setHoveredInboxItem(itemKey)}
                          onHoverEnd={() => setHoveredInboxItem(null)}
                          className="relative overflow-hidden rounded-xl border-2"
                          style={{
                            borderTopColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderRightColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderBottomColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderLeftColor: accent,
                            borderLeftWidth: dm ? '2px' : '4px',
                            background: dm ? `linear-gradient(135deg, ${accent}15, ${accent}06)` : (isHov ? `${accent}08` : 'white'),
                            boxShadow: dm ? (isHov ? `0 4px 20px ${accent}25` : `0 1px 4px ${accent}08`) : (isHov ? `0 4px 16px ${accent}30, 0 1px 4px rgba(0,0,0,0.06)` : '0 1px 4px rgba(0,0,0,0.06)'),
                            transition: 'border-color 0.2s, box-shadow 0.2s, background 0.2s',
                          }}
                        >
                          <div className="p-3 flex items-center justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0"
                                style={{ backgroundColor: `${accent}25`, color: accent, border: `1px solid ${accent}50` }}>
                                {orphan.name.charAt(0)}
                              </div>
                              <div className="min-w-0">
                                <p className={`text-sm font-semibold ${textPrimary} truncate`}>{orphan.name}</p>
                                <p className="text-sm mt-1.5 font-medium" style={{ color: accent }}>
                                  {orphan.task_count} {orphan.task_count === 1 ? 'task' : 'tasks'} in Jira · Not a member
                                </p>
                              </div>
                            </div>
                            <button
                              onClick={() => { setInviteEmail(""); setInviteJiraAccountId(orphan.account_id); setInviteError(""); setShowInviteModal(true) }}
                              className="text-xs px-3 py-1.5 rounded-lg font-semibold flex-shrink-0 transition-all"
                              style={{ background: `${accent}20`, color: accent, border: `1px solid ${accent}50` }}>
                              Invite
                            </button>
                          </div>
                          <motion.div className="absolute bottom-0 left-0 right-0 h-[2px]"
                            style={{ background: `linear-gradient(90deg, ${accent}, ${accent}50)`, transformOrigin: 'left' }}
                            initial={{ scaleX: 0 }} animate={{ scaleX: isHov ? 1 : 0 }} transition={{ duration: 0.25 }} />
                        </motion.div>
                      )
                    })}

                    {/* Project Charter - Temporarily hidden */}
                    {false && (showPmBoardInitialLoading ? null : charterStatus && (() => {
                      const incompleteStages: string[] = []
                      if (charterStatus.stages.goal !== 'finalized') incompleteStages.push('Goals')
                      if (charterStatus.stages.scope !== 'finalized') incompleteStages.push('Scope')
                      if (charterStatus.stages.requirements !== 'finalized') incompleteStages.push('Requirements')
                      if (charterStatus.stages.features_tasks !== 'finalized') incompleteStages.push('Features & Tasks')
                      if (incompleteStages.length === 0) return null
                      const accent = '#eab308'
                      const itemKey = 'charter'
                      const isHov = hoveredInboxItem === itemKey
                      return (
                        <motion.div
                          initial={{ opacity: 0, x: -30 }} animate={{ opacity: 1, x: 0 }}
                          transition={{ duration: 0.35, delay: (pendingInvites.length + orphanAssignees.length) * 0.08, ease: [0.4, 0, 0.2, 1] }}
                          whileHover={{ y: -2 }}
                          onHoverStart={() => setHoveredInboxItem(itemKey)}
                          onHoverEnd={() => setHoveredInboxItem(null)}
                          onClick={() => setActiveNav("Project Charter")}
                          className="relative overflow-hidden rounded-xl border-2 cursor-pointer"
                          style={{
                            borderTopColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderRightColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderBottomColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderLeftColor: accent,
                            borderLeftWidth: dm ? '2px' : '4px',
                            background: dm ? `linear-gradient(135deg, ${accent}15, ${accent}06)` : (isHov ? `${accent}08` : 'white'),
                            boxShadow: dm ? (isHov ? `0 4px 20px ${accent}25` : `0 1px 4px ${accent}08`) : (isHov ? `0 4px 16px ${accent}30, 0 1px 4px rgba(0,0,0,0.06)` : '0 1px 4px rgba(0,0,0,0.06)'),
                            transition: 'border-color 0.2s, box-shadow 0.2s, background 0.2s',
                          }}
                        >
                          <div className="p-3 flex items-center justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
                                style={{ backgroundColor: `${accent}25`, border: `1px solid ${accent}50` }}>
                                <svg className="w-4 h-4" style={{ color: accent }} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" />
                                </svg>
                              </div>
                              <div className="min-w-0">
                                <p className={`text-sm font-semibold ${textPrimary}`}>Complete Project Charter</p>
                                <p className="text-sm mt-1.5 font-medium truncate" style={{ color: accent }}>Missing: {incompleteStages.join(', ')}</p>
                              </div>
                            </div>
                            <button className="text-xs px-3 py-1.5 rounded-lg font-semibold flex-shrink-0 transition-all"
                              style={{ background: `${accent}20`, color: accent, border: `1px solid ${accent}50` }}>
                              Complete
                            </button>
                          </div>
                          <motion.div className="absolute bottom-0 left-0 right-0 h-[2px]"
                            style={{ background: `linear-gradient(90deg, ${accent}, ${accent}50)`, transformOrigin: 'left' }}
                            initial={{ scaleX: 0 }} animate={{ scaleX: isHov ? 1 : 0 }} transition={{ duration: 0.25 }} />
                        </motion.div>
                      )
                    })())}

                    {/* Jira Integration */}
                    {showPmBoardInitialLoading ? null : (() => {
                      const jiraIntegration = getIntegration(selectedProject, 'jira')
                      if (jiraIntegration && jiraIntegration.status === 'connected') return null
                      const accent = '#a78bfa'
                      const itemKey = 'jira-connect'
                      const isHov = hoveredInboxItem === itemKey
                      const baseDelay = (pendingInvites.length + orphanAssignees.length + (charterStatus ? 1 : 0)) * 0.08
                      return (
                        <motion.div
                          initial={{ opacity: 0, x: -30 }} animate={{ opacity: 1, x: 0 }}
                          transition={{ duration: 0.35, delay: baseDelay, ease: [0.4, 0, 0.2, 1] }}
                          whileHover={{ y: -2 }}
                          onHoverStart={() => setHoveredInboxItem(itemKey)}
                          onHoverEnd={() => setHoveredInboxItem(null)}
                          onClick={() => openControlPanelTab("Integrate Tools")}
                          className="relative overflow-hidden rounded-xl border-2 cursor-pointer"
                          style={{
                            borderTopColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderRightColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderBottomColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderLeftColor: accent,
                            borderLeftWidth: dm ? '2px' : '4px',
                            background: dm ? `linear-gradient(135deg, ${accent}15, ${accent}06)` : (isHov ? `${accent}08` : 'white'),
                            boxShadow: dm ? (isHov ? `0 4px 20px ${accent}25` : `0 1px 4px ${accent}08`) : (isHov ? `0 4px 16px ${accent}30, 0 1px 4px rgba(0,0,0,0.06)` : '0 1px 4px rgba(0,0,0,0.06)'),
                            transition: 'border-color 0.2s, box-shadow 0.2s, background 0.2s',
                          }}
                        >
                          <div className="p-3 flex items-center justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
                                style={{ backgroundColor: `${accent}25`, border: `1px solid ${accent}50` }}>
                                <svg className="w-4 h-4" style={{ color: accent }} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
                                </svg>
                              </div>
                              <div className="min-w-0">
                                <p className={`text-sm font-semibold ${textPrimary}`}>Connect Jira</p>
                                <p className="text-sm mt-1.5 font-medium" style={{ color: accent }}>Sync tasks and track progress</p>
                              </div>
                            </div>
                            <button className="text-xs px-3 py-1.5 rounded-lg font-semibold flex-shrink-0 transition-all"
                              style={{ background: `${accent}20`, color: accent, border: `1px solid ${accent}50` }}>
                              Connect
                            </button>
                          </div>
                          <motion.div className="absolute bottom-0 left-0 right-0 h-[2px]"
                            style={{ background: `linear-gradient(90deg, ${accent}, ${accent}50)`, transformOrigin: 'left' }}
                            initial={{ scaleX: 0 }} animate={{ scaleX: isHov ? 1 : 0 }} transition={{ duration: 0.25 }} />
                        </motion.div>
                      )
                    })()}

                    {/* Slack Integration */}
                    {showPmBoardInitialLoading ? null : (() => {
                      const slackIntegration = getIntegration(selectedProject, 'slack')
                      if (slackIntegration && slackIntegration.status === 'connected') return null
                      const accent = '#78a530'
                      const itemKey = 'slack-connect'
                      const isHov = hoveredInboxItem === itemKey
                      const baseDelay = (pendingInvites.length + orphanAssignees.length + (charterStatus ? 1 : 0) + 1) * 0.08
                      return (
                        <motion.div
                          initial={{ opacity: 0, x: -30 }} animate={{ opacity: 1, x: 0 }}
                          transition={{ duration: 0.35, delay: baseDelay, ease: [0.4, 0, 0.2, 1] }}
                          whileHover={{ y: -2 }}
                          onHoverStart={() => setHoveredInboxItem(itemKey)}
                          onHoverEnd={() => setHoveredInboxItem(null)}
                          onClick={() => openControlPanelTab("Integrate Tools")}
                          className="relative overflow-hidden rounded-xl border-2 cursor-pointer"
                          style={{
                            borderTopColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderRightColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderBottomColor: dm ? (isHov ? accent : `${accent}40`) : 'rgba(0,0,0,0.18)',
                            borderLeftColor: accent,
                            borderLeftWidth: dm ? '2px' : '4px',
                            background: dm ? `linear-gradient(135deg, ${accent}15, ${accent}06)` : (isHov ? `${accent}08` : 'white'),
                            boxShadow: dm ? (isHov ? `0 4px 20px ${accent}25` : `0 1px 4px ${accent}08`) : (isHov ? `0 4px 16px ${accent}30, 0 1px 4px rgba(0,0,0,0.06)` : '0 1px 4px rgba(0,0,0,0.06)'),
                            transition: 'border-color 0.2s, box-shadow 0.2s, background 0.2s',
                          }}
                        >
                          <div className="p-3 flex items-center justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
                                style={{ backgroundColor: `${accent}25`, border: `1px solid ${accent}50` }}>
                                <svg className="w-4 h-4" style={{ color: accent }} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
                                </svg>
                              </div>
                              <div className="min-w-0">
                                <p className={`text-sm font-semibold ${textPrimary}`}>Connect Slack</p>
                                <p className="text-sm mt-1.5 font-medium" style={{ color: accent }}>Send reminders and updates</p>
                              </div>
                            </div>
                            <button className="text-xs px-3 py-1.5 rounded-lg font-semibold flex-shrink-0 transition-all"
                              style={{ background: `${accent}20`, color: accent, border: `1px solid ${accent}50` }}>
                              Connect
                            </button>
                          </div>
                          <motion.div className="absolute bottom-0 left-0 right-0 h-[2px]"
                            style={{ background: `linear-gradient(90deg, ${accent}, ${accent}50)`, transformOrigin: 'left' }}
                            initial={{ scaleX: 0 }} animate={{ scaleX: isHov ? 1 : 0 }} transition={{ duration: 0.25 }} />
                        </motion.div>
                      )
                    })()}

                  </div>
                </div>
              </div>
              )}

            </div>
          )}

          {/* Project Charter Content - Temporarily hidden */}
          {false && activeNav === "Project Charter" && selectedProject && (
            <div>
              <PlannerContent
                projectId={selectedProject!.project_id}
                projectName={selectedProject!.project_name}
                isDarkMode={isDarkMode}
              />
            </div>
          )}

          {/* Project Brain Content - Temporarily hidden for demo */}
          {false && activeNav === "Project Brain" && (
            <div className="h-full flex flex-col max-w-4xl mx-auto">
              {/* Compact Header */}
              <div className="flex items-center gap-4 mb-4 flex-shrink-0">
                <div className="w-12 h-12 bg-gradient-to-br from-[#78a530] to-[#5a7d24] rounded-xl flex items-center justify-center shadow-lg">
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-white" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                  </svg>
                </div>
                <div>
                  <h1 className="text-2xl font-bold text-gray-900">Ask Marshal</h1>
                  <p className="text-base text-gray-600">Ask anything about your project and I'll help you find answers</p>
                </div>
              </div>

              {/* Chat Container - Takes remaining height */}
              <div className="flex-1 flex flex-col bg-white rounded-xl border border-gray-300 shadow-sm overflow-hidden min-h-0">
                {/* Chat Messages Area */}
                <div className="flex-1 overflow-y-auto p-4">
                  {/* Welcome Message - Compact */}
                  <div className="flex gap-3 mb-4">
                    <div className="w-8 h-8 rounded-lg bg-[#78a530] flex items-center justify-center flex-shrink-0">
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-white" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                      </svg>
                    </div>
                    <div className="flex-1">
                      <p className="text-sm font-medium text-gray-500 mb-1">Marshal</p>
                      <div className={`rounded-xl rounded-tl-none p-3 ${dm ? "bg-white/[0.06]" : "bg-gray-100"}`}>
                        <p className={`text-base ${dm ? "text-gray-200" : "text-gray-800"}`}>
                          Hi! 👋 I'm your intelligent project assistant with access to all your project data – goals, tasks, team info & connected tools.
                          Ask me anything about <span className="font-semibold text-[#78a530]">{selectedProject?.project_name || 'your project'}</span>!
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Suggested Questions - More Compact */}
                  <div>
                    <p className="text-sm font-medium text-gray-400 mb-2 uppercase tracking-wide">Try asking</p>
                    <div className="grid grid-cols-2 gap-2">
                      <button className="text-left px-3 py-2 bg-gray-50 hover:bg-[#78a530]/10 border border-gray-300 hover:border-[#78a530] rounded-lg text-xs text-gray-700 hover:text-[#78a530] transition">
                        What's the project status?
                      </button>
                      <button className="text-left px-3 py-2 bg-gray-50 hover:bg-[#78a530]/10 border border-gray-300 hover:border-[#78a530] rounded-lg text-xs text-gray-700 hover:text-[#78a530] transition">
                        Who is working on what?
                      </button>
                      <button className="text-left px-3 py-2 bg-gray-50 hover:bg-[#78a530]/10 border border-gray-300 hover:border-[#78a530] rounded-lg text-xs text-gray-700 hover:text-[#78a530] transition">
                        What are the blockers?
                      </button>
                      <button className="text-left px-3 py-2 bg-gray-50 hover:bg-[#78a530]/10 border border-gray-300 hover:border-[#78a530] rounded-lg text-xs text-gray-700 hover:text-[#78a530] transition">
                        Summarize project goals
                      </button>
                    </div>
                  </div>
                </div>

                {/* Input Area - Compact */}
                <div className={`border-t p-3 flex-shrink-0 ${dm ? "border-white/[0.06] bg-[#192035]" : "border-gray-200 bg-gray-50"}`}>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="Ask Marshal anything about your project..."
                      className="flex-1 px-3 py-2 bg-white border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent text-sm"
                    />
                    <button className="w-9 h-9 bg-[#78a530] hover:bg-[#6b9429] rounded-lg flex items-center justify-center transition flex-shrink-0">
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}



          {/* Forecast Content - Temporarily hidden */}
          {false && activeNav === "Forecast" && (
            <div className="h-full flex flex-col">

              {/* Floating Tab Menu — visible, not blurred */}
              <FloatingTabMenu isDarkMode={isDarkMode}>
                {["Risks", "Blockers", "Completion Forecast", "Mind Map"].map((tab) => (
                  <FloatingTabButton
                    key={tab}
                    isDarkMode={isDarkMode}
                    active={activeForecastTab === tab}
                    onClick={() => setActiveForecastTab(tab)}
                  >
                    {tab}
                  </FloatingTabButton>
                ))}
              </FloatingTabMenu>

              {/* Sample Data banner */}
              <div className="flex justify-center mb-4">
                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${dm ? "bg-amber-900/30 text-amber-300 border-amber-700/40" : "bg-amber-50 text-amber-700 border-amber-200"}`}>
                  <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" /></svg>
                  Sample Data
                </span>
              </div>

              {/* Content Area */}
              <div className="flex-1 overflow-hidden min-h-0 flex flex-col">
                {activeForecastTab === "Risks" && (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {/* Active Risks */}
                    <div className={`${cardBg} ${cardBorder} rounded-xl border flex flex-col overflow-hidden`} style={cardStyle}>
                      <div className={`px-5 py-3.5 border-b ${cardHeaderBorder} flex items-center gap-2`} style={cardHeaderStyle}>
                        <span className={`w-2 h-2 rounded-full bg-red-500 flex-shrink-0`} />
                        <h3 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Active Risks</h3>
                      </div>
                      <div className="p-4 space-y-3">
                        <div className={`border rounded-xl p-4 ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
                          <div className="flex items-start justify-between mb-2">
                            <div>
                              <h4 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Scope Creep Risk</h4>
                              <p className={`text-xs mt-0.5 ${dm ? "text-gray-500" : "text-gray-500"}`}>New features added without timeline adjustment</p>
                            </div>
                            <span className={`px-2 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide flex-shrink-0 ${dm ? "bg-red-900/40 text-red-400" : "bg-red-100 text-red-700"}`}>High</span>
                          </div>
                          <p className={`text-xs p-2.5 rounded-lg ${dm ? "bg-[#192035] text-gray-400" : "bg-gray-50 text-gray-600"}`}>
                            4 new features added to Sprint 4 on Jan 22. Estimated impact: +3 days to completion.
                          </p>
                        </div>
                        <div className={`border rounded-xl p-4 ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
                          <div className="flex items-start justify-between mb-2">
                            <div>
                              <h4 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Resource Constraint</h4>
                              <p className={`text-xs mt-0.5 ${dm ? "text-gray-500" : "text-gray-500"}`}>Frontend team capacity overload</p>
                            </div>
                            <span className={`px-2 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide flex-shrink-0 ${dm ? "bg-orange-900/40 text-orange-400" : "bg-orange-100 text-orange-700"}`}>Medium</span>
                          </div>
                          <p className={`text-xs p-2.5 rounded-lg ${dm ? "bg-[#192035] text-gray-400" : "bg-gray-50 text-gray-600"}`}>
                            2 senior developers at 120% capacity. Risk of burnout or quality issues increasing.
                          </p>
                        </div>
                      </div>
                    </div>

                    {/* Predicted Risks */}
                    <div className={`${cardBg} ${cardBorder} rounded-xl border flex flex-col overflow-hidden`} style={cardStyle}>
                      <div className={`px-5 py-3.5 border-b ${cardHeaderBorder} flex items-center gap-2`} style={cardHeaderStyle}>
                        <span className={`w-2 h-2 rounded-full bg-blue-500 flex-shrink-0`} />
                        <h3 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Predicted Risks</h3>
                        <span className={`ml-auto text-xs px-2 py-0.5 rounded-full ${dm ? "bg-blue-900/30 text-blue-400" : "bg-blue-50 text-blue-600"}`}>AI</span>
                      </div>
                      <div className="p-4 space-y-3">
                        <div className={`border rounded-xl p-4 ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
                          <div className="flex items-start justify-between mb-2">
                            <div>
                              <h4 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Potential Integration Delay</h4>
                              <p className={`text-xs mt-0.5 ${dm ? "text-gray-500" : "text-gray-500"}`}>Third-party API integrations typically take 2x estimates</p>
                            </div>
                            <span className={`px-2 py-0.5 rounded-full text-xs font-bold flex-shrink-0 ${dm ? "bg-blue-900/40 text-blue-400" : "bg-blue-100 text-blue-700"}`}>AI</span>
                          </div>
                          <p className={`text-xs p-2.5 rounded-lg ${dm ? "bg-[#192035] text-gray-400" : "bg-gray-50 text-gray-600"}`}>
                            Based on Payment Gateway task history, this ticket is likely to slip by 4 days.
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {activeForecastTab === "Blockers" && (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {/* Active Blockers */}
                    <div className={`${cardBg} ${cardBorder} rounded-xl border flex flex-col overflow-hidden`} style={cardStyle}>
                      <div className={`px-5 py-3.5 border-b ${cardHeaderBorder} flex items-center gap-2`} style={cardHeaderStyle}>
                        <span className="w-2 h-2 rounded-full bg-red-500 flex-shrink-0" />
                        <h3 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Active Blockers</h3>
                      </div>
                      <div className="p-4 space-y-3">
                        <div className={`border rounded-xl p-4 ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
                          <div className="flex items-start justify-between mb-2">
                            <h4 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>API Integration Module</h4>
                            <span className={`text-xs px-2 py-0.5 rounded border flex-shrink-0 ${dm ? "bg-white/[0.06] text-gray-400 border-white/[0.12]" : "bg-gray-100 text-gray-600 border-gray-200"}`}>3 days</span>
                          </div>
                          <p className={`text-xs mb-2 ${dm ? "text-gray-500" : "text-gray-500"}`}>Assigned to: <span className={`font-medium ${dm ? "text-gray-300" : "text-gray-700"}`}>John Doe</span></p>
                          <p className={`text-xs p-2.5 rounded-lg ${dm ? "bg-[#192035] text-gray-400" : "bg-gray-50 text-gray-600"}`}>
                            Waiting for third-party API credentials from security team. Request ID: REQ-992.
                          </p>
                        </div>
                      </div>
                    </div>

                    {/* Predicted Blockers */}
                    <div className={`${cardBg} ${cardBorder} rounded-xl border flex flex-col overflow-hidden`} style={cardStyle}>
                      <div className={`px-5 py-3.5 border-b ${cardHeaderBorder} flex items-center gap-2`} style={cardHeaderStyle}>
                        <span className="w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                        <h3 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Predicted Blockers</h3>
                        <span className={`ml-auto text-xs px-2 py-0.5 rounded-full ${dm ? "bg-blue-900/30 text-blue-400" : "bg-blue-50 text-blue-600"}`}>AI</span>
                      </div>
                      <div className="p-4 space-y-3">
                        <div className={`border rounded-xl p-4 ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
                          <div className="flex items-start justify-between mb-2">
                            <h4 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"} flex items-center gap-2 flex-wrap`}>
                              Database Migration Approval
                              <span className={`text-xs px-1.5 py-0.5 rounded ${dm ? "bg-blue-900/40 text-blue-400" : "bg-blue-100 text-blue-700"}`}>High Confidence</span>
                            </h4>
                          </div>
                          <p className={`text-xs mb-2 ${dm ? "text-gray-500" : "text-gray-500"}`}>Predicted to block <span className={`font-medium ${dm ? "text-gray-300" : "text-gray-700"}`}>Backend Team</span> in 2 days</p>
                          <p className={`text-xs p-2.5 rounded-lg ${dm ? "bg-[#192035] text-gray-400" : "bg-gray-50 text-gray-600"}`}>
                            Schema reviews are averaging 4 days but migration is scheduled to start in 2 days.
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {activeForecastTab === "Completion Forecast" && (
                  <div className={`${cardBg} ${cardBorder} rounded-xl border flex flex-col h-full overflow-hidden`} style={cardStyle}>
                    <div className={`px-6 py-4 border-b ${cardHeaderBorder} flex justify-between items-center`} style={cardHeaderStyle}>
                      <h2 className={`text-lg font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Completion Forecast</h2>
                      <span className={`px-3 py-1 rounded-full text-sm font-medium ${dm ? "bg-green-900/40 text-green-400" : "bg-green-100 text-green-700"}`}>92% Confidence</span>
                    </div>
                    <div className="flex-1 overflow-y-auto p-6">
                      <div className="mb-8">
                        <div className="flex justify-between items-end mb-2">
                          <div>
                            <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"} font-medium uppercase tracking-wide`}>Overall Sprint Progress</p>
                            <h3 className={`text-3xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>68% <span className={`text-base font-normal ${dm ? "text-gray-400" : "text-gray-500"}`}>complete</span></h3>
                          </div>
                          <span className={`text-sm font-medium ${dm ? "text-green-400" : "text-green-600"}`}>+12% from last week</span>
                        </div>
                        <div className={`w-full h-3 ${dm ? "bg-white/[0.08]" : "bg-gray-100"} rounded-full overflow-hidden`}>
                          <div className="h-full bg-gradient-to-r from-[#78a530] to-[#6b9429] rounded-full" style={{ width: "68%" }}></div>
                        </div>
                        <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"} mt-2 text-right`}>17 of 25 tasks completed</p>
                      </div>

                      <div className="grid grid-cols-3 gap-6 mb-8">
                        <div className={`p-4 rounded-xl border ${dm ? "bg-green-900/20 border-green-700/30" : "bg-green-50 border-green-100"}`}>
                          <p className={`text-xs mb-1 ${dm ? "text-gray-400" : "text-gray-500"}`}>Optimistic Case</p>
                          <p className={`text-lg font-bold ${dm ? "text-green-400" : "text-green-700"}`}>Jan 28, 2026</p>
                          <span className={`inline-block mt-2 text-xs px-2 py-0.5 rounded border ${dm ? "bg-green-900/30 text-green-400 border-green-700/30" : "bg-white text-green-700 border-green-200"}`}>If no new blockers</span>
                        </div>
                        <div className={`${cardBg} p-4 rounded-xl border-2 border-[#78a530] shadow-sm relative overflow-hidden`}>
                          <div className="absolute top-0 right-0 bg-[#78a530] text-white text-xs uppercase font-bold px-2 py-1 rounded-bl-lg">Most Likely</div>
                          <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"} mb-1`}>Realistic Projection</p>
                          <p className={`text-lg font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>Jan 31, 2026</p>
                          <span className={`inline-block mt-2 text-xs px-2 py-0.5 rounded ${dm ? "bg-white/[0.08] text-gray-400" : "bg-gray-100 text-gray-700"}`}>Current Velocity</span>
                        </div>
                        <div className={`p-4 rounded-xl border ${dm ? "bg-orange-900/20 border-orange-700/30" : "bg-orange-50 border-orange-100"}`}>
                          <p className={`text-xs mb-1 ${dm ? "text-gray-400" : "text-gray-500"}`}>Pessimistic Case</p>
                          <p className={`text-lg font-bold ${dm ? "text-orange-400" : "text-orange-700"}`}>Feb 4, 2026</p>
                          <span className={`inline-block mt-2 text-xs px-2 py-0.5 rounded border ${dm ? "bg-orange-900/30 text-orange-400 border-orange-700/30" : "bg-white text-orange-700 border-orange-200"}`}>If risks materialize</span>
                        </div>
                      </div>

                      <div className={`${cardBg} ${cardBorder} rounded-xl p-5 border`}>
                        <h4 className={`font-semibold ${dm ? "text-gray-100" : "text-gray-900"} mb-3`}>AI Insights</h4>
                        <ul className={`space-y-2 text-sm ${dm ? "text-gray-300" : "text-gray-600"}`}>
                          <li className="flex items-start gap-2">
                            <span className="text-[#78a530] font-bold mt-0.5">✓</span>
                            Development velocity is trending upwards (15% increase vs last sprint).
                          </li>
                          <li className="flex items-start gap-2">
                            <span className="text-orange-500 font-bold mt-0.5">!</span>
                            Backend API completion is the critical path. Any delay here pushes the whole release.
                          </li>
                          <li className="flex items-start gap-2">
                            <span className="text-gray-400 font-bold mt-0.5">•</span>
                            QA likely to be squeezed. Recommend starting integration tests early.
                          </li>
                        </ul>
                      </div>
                    </div>
                  </div>
                )}

                {activeForecastTab === "Mind Map" && (
                  <div className="flex-1 min-h-0">
                    <ForecastMindMap isDarkMode={isDarkMode} />
                  </div>
                )}

              </div>
            </div>
          )}

          {/* ── REPORTS (static placeholder — replace with real data later) ── */}
          {false && activeNav === "Reports" && (
            <div className="h-full flex flex-col overflow-y-auto">
                  <div className="flex flex-col gap-4 pb-6">

                    {/* Sample data + sub-menu + actions row */}
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      {/* Sub-menu */}
                      <div className={`inline-flex items-center gap-1 rounded-lg border p-1 ${dm ? "bg-[#080d1a] border-white/[0.08]" : "bg-white border-gray-200"}`}>
                        {["Weekly Summary"].map(t => (
                          <button
                            key={t}
                            type="button"
                            onClick={() => setActiveReportsTab(t)}
                            className={`px-3 py-1.5 rounded-md text-xs font-semibold transition ${activeReportsTab === t ? "bg-[#78a530] text-white shadow-sm" : dm ? "text-gray-400 hover:text-gray-200 hover:bg-white/[0.06]" : "text-gray-500 hover:text-gray-800 hover:bg-gray-100"}`}
                          >
                            {t}
                          </button>
                        ))}
                      </div>

                      {/* Sample Data badge — centered */}
                      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${dm ? "bg-amber-900/30 text-amber-300 border-amber-700/40" : "bg-amber-50 text-amber-700 border-amber-200"}`}>
                        <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" /></svg>
                        Sample Data
                      </span>

                      {/* Template / Upload buttons */}
                      <div className="flex items-center gap-2">
                        <button type="button" className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition ${dm ? "border-white/[0.12] text-gray-300 hover:bg-white/[0.06]" : "border-gray-200 text-gray-600 hover:bg-gray-100"}`}>
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z" /></svg>
                          Choose Template
                        </button>
                        <button type="button" className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition ${dm ? "border-white/[0.12] text-gray-300 hover:bg-white/[0.06]" : "border-gray-200 text-gray-600 hover:bg-gray-100"}`}>
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" /></svg>
                          Upload Sample
                        </button>
                      </div>
                    </div>

                    {/* Email container */}
                    {activeReportsTab === "Weekly Summary" && (
                      <div className={`rounded-2xl border overflow-hidden ${dm ? "border-white/[0.08]" : "border-gray-200"}`} style={{ boxShadow: dm ? "0 4px 24px rgba(0,0,0,0.4)" : "0 4px 24px rgba(0,0,0,0.07)" }}>

                        {/* Email chrome */}
                        <div className={`px-6 py-4 border-b ${dm ? "bg-[#0d1225] border-white/[0.06]" : "bg-gray-50 border-gray-200"}`}>
                          <div className="flex items-center justify-between mb-3">
                            <p className={`text-xs font-semibold uppercase tracking-wide ${dm ? "text-gray-500" : "text-gray-400"}`}>Email Preview</p>
                            <div className="flex gap-1.5">
                              <span className="w-3 h-3 rounded-full bg-red-400" />
                              <span className="w-3 h-3 rounded-full bg-amber-400" />
                              <span className="w-3 h-3 rounded-full bg-green-400" />
                            </div>
                          </div>
                          <div className={`space-y-1.5 text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>
                            <div className="flex gap-2"><span className="font-semibold w-12">From:</span><span>ProMarshal Reports &lt;reports@promarshal.ai&gt;</span></div>
                            <div className="flex gap-2"><span className="font-semibold w-12">To:</span><span>stakeholders@company.com, customer@client.com</span></div>
                            <div className="flex gap-2"><span className="font-semibold w-12">Subject:</span><span className={`font-semibold ${dm ? "text-gray-200" : "text-gray-700"}`}>Weekly Project Report — Customer Portal App (Week of Mar 31, 2026)</span></div>
                          </div>
                        </div>

                        {/* Email body */}
                        <div className={`px-8 py-6 ${dm ? "bg-[#080d1a]" : "bg-white"}`}>

                          {/* Header */}
                          <div className="flex items-center justify-between mb-6 pb-5 border-b" style={{ borderColor: dm ? "rgba(255,255,255,0.07)" : "#e5e7eb" }}>
                            <div>
                              <h2 className={`text-xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>Weekly Project Report</h2>
                              <p className={`text-sm mt-0.5 ${dm ? "text-gray-500" : "text-gray-500"}`}>Customer Portal App &nbsp;&middot;&nbsp; Week of Mar 31 – Apr 6, 2026</p>
                            </div>
                            <div className="text-right">
                              <p className={`text-xs font-semibold ${dm ? "text-[#78a530]" : "text-[#78a530]"}`}>ProMarshal</p>
                              <p className={`text-xs ${dm ? "text-gray-600" : "text-gray-400"}`}>Automated Report</p>
                            </div>
                          </div>

                          {/* Work Item Count Chart */}
                          <div className="mb-6">
                            <p className={`text-xs font-semibold uppercase tracking-wide mb-3 ${dm ? "text-gray-500" : "text-gray-400"}`}>Work Item Status</p>
                            <div className="grid grid-cols-4 gap-3 mb-3">
                              {[
                                { label: "Completed", count: 12, color: "#78a530", bg: dm ? "bg-[#78a530]/10" : "bg-[#78a530]/10" },
                                { label: "In Progress", count: 8,  color: "#2563eb", bg: dm ? "bg-blue-900/20" : "bg-blue-50" },
                                { label: "Blocked",    count: 2,  color: "#ef4444", bg: dm ? "bg-red-900/20"  : "bg-red-50"  },
                                { label: "Not Started",count: 5,  color: "#6b7280", bg: dm ? "bg-white/[0.04]": "bg-gray-50"  },
                              ].map(s => (
                                <div key={s.label} className={`rounded-xl p-3 text-center ${s.bg}`}>
                                  <p className="text-2xl font-bold" style={{ color: s.color }}>{s.count}</p>
                                  <p className={`text-xs mt-0.5 ${dm ? "text-gray-500" : "text-gray-500"}`}>{s.label}</p>
                                </div>
                              ))}
                            </div>
                            {/* Stacked bar */}
                            <div className="flex rounded-full overflow-hidden h-2">
                              <div className="h-full" style={{ width: "44%", background: "#78a530" }} title="Completed" />
                              <div className="h-full" style={{ width: "30%", background: "#2563eb" }} title="In Progress" />
                              <div className="h-full" style={{ width: "7%",  background: "#ef4444" }} title="Blocked" />
                              <div className="h-full" style={{ width: "19%", background: "#6b7280" }} title="Not Started" />
                            </div>
                            <div className="flex gap-4 mt-1.5">
                              {[["#78a530","Completed 44%"],["#2563eb","In Progress 30%"],["#ef4444","Blocked 7%"],["#6b7280","Not Started 19%"]].map(([c,l]) => (
                                <div key={l} className="flex items-center gap-1">
                                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: c }} />
                                  <span className={`text-xs ${dm ? "text-gray-500" : "text-gray-400"}`}>{l}</span>
                                </div>
                              ))}
                            </div>
                          </div>

                          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
                            {/* Highlights & Achievements */}
                            <div>
                              <p className={`text-xs font-semibold uppercase tracking-wide mb-2.5 ${dm ? "text-gray-500" : "text-gray-400"}`}>Highlights & Achievements</p>
                              <ul className="space-y-2">
                                {[
                                  "User authentication module shipped — all test cases passed",
                                  "Dashboard redesign approved by customer in review session",
                                  "Jira sync integration live in staging environment",
                                  "Sprint velocity up 18% compared to previous sprint",
                                ].map((item, i) => (
                                  <li key={i} className="flex items-start gap-2">
                                    <svg className="w-4 h-4 text-[#78a530] flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
                                    <span className={`text-sm ${dm ? "text-gray-300" : "text-gray-600"}`}>{item}</span>
                                  </li>
                                ))}
                              </ul>
                            </div>

                            {/* Attention Needed */}
                            <div>
                              <p className={`text-xs font-semibold uppercase tracking-wide mb-2.5 ${dm ? "text-gray-500" : "text-gray-400"}`}>Attention Needed from Stakeholders</p>
                              <ul className="space-y-2">
                                {[
                                  { text: "Payment gateway vendor credentials — escalation required", level: "High" },
                                  { text: "PDF export reprioritisation changes Sprint 5 scope — sign-off needed", level: "High" },
                                  { text: "UAT sign-off criteria to be shared by Apr 9", level: "Medium" },
                                ].map((item, i) => (
                                  <li key={i} className="flex items-start gap-2">
                                    <span className={`mt-0.5 flex-shrink-0 text-xs font-bold px-1.5 py-0.5 rounded ${item.level === "High" ? dm ? "bg-red-900/40 text-red-400" : "bg-red-100 text-red-600" : dm ? "bg-amber-900/30 text-amber-400" : "bg-amber-50 text-amber-600"}`}>{item.level}</span>
                                    <span className={`text-sm ${dm ? "text-gray-300" : "text-gray-600"}`}>{item.text}</span>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          </div>

                          {/* Risks & Blockers summary */}
                          <div className={`rounded-xl p-4 mb-6 border ${dm ? "bg-[#0d1225] border-white/[0.06]" : "bg-gray-50 border-gray-200"}`}>
                            <p className={`text-xs font-semibold uppercase tracking-wide mb-2.5 ${dm ? "text-gray-500" : "text-gray-400"}`}>Risks & Blockers Summary</p>
                            <div className="grid grid-cols-2 gap-3">
                              <div className="flex items-center gap-2">
                                <span className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${dm ? "bg-red-900/30" : "bg-red-50"}`}>
                                  <svg className={`w-4 h-4 ${dm ? "text-red-400" : "text-red-500"}`} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126Z" /></svg>
                                </span>
                                <div><p className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>2 Active Risks</p><p className={`text-xs ${dm ? "text-gray-500" : "text-gray-400"}`}>1 High · 1 Medium</p></div>
                              </div>
                              <div className="flex items-center gap-2">
                                <span className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${dm ? "bg-orange-900/30" : "bg-orange-50"}`}>
                                  <svg className={`w-4 h-4 ${dm ? "text-orange-400" : "text-orange-500"}`} fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" /></svg>
                                </span>
                                <div><p className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>1 Active Blocker</p><p className={`text-xs ${dm ? "text-gray-500" : "text-gray-400"}`}>Vendor creds · 3 days blocked</p></div>
                              </div>
                            </div>
                          </div>

                          {/* Next week focus */}
                          <div className="mb-4">
                            <p className={`text-xs font-semibold uppercase tracking-wide mb-2.5 ${dm ? "text-gray-500" : "text-gray-400"}`}>Next Week Focus</p>
                            <ul className="space-y-1.5">
                              {[
                                "Complete PDF export feature and move to QA",
                                "Resolve payment gateway blocker — vendor escalation",
                                "Prepare sprint plan with revised scope for Apr 9 planning session",
                                "Share stable staging URL for customer UAT",
                              ].map((item, i) => (
                                <li key={i} className="flex items-start gap-2">
                                  <span className={`mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${dm ? "bg-gray-500" : "bg-gray-300"}`} />
                                  <span className={`text-sm ${dm ? "text-gray-400" : "text-gray-600"}`}>{item}</span>
                                </li>
                              ))}
                            </ul>
                          </div>

                          {/* Footer */}
                          <div className={`pt-4 border-t text-xs ${dm ? "border-white/[0.06] text-gray-600" : "border-gray-100 text-gray-400"}`}>
                            Generated by ProMarshal &nbsp;&middot;&nbsp; Apr 7, 2026 &nbsp;&middot;&nbsp; This report was auto-generated from live project data.
                          </div>
                        </div>

                        {/* Approve / Send action bar */}
                        <div className={`px-6 py-4 border-t flex items-center justify-between gap-3 ${dm ? "bg-[#0d1225] border-white/[0.06]" : "bg-gray-50 border-gray-200"}`}>
                          <p className={`text-xs ${dm ? "text-gray-500" : "text-gray-400"}`}>Review the report before sending to stakeholders.</p>
                          <div className="flex items-center gap-2">
                            <button type="button" className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold border border-[#2563eb]/40 text-[#2563eb] hover:bg-[#2563eb]/10 transition">
                              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path fillRule="evenodd" d="M9 4.5a.75.75 0 01.721.544l.813 2.846a3.75 3.75 0 002.576 2.576l2.846.813a.75.75 0 010 1.442l-2.846.813a3.75 3.75 0 00-2.576 2.576l-.813 2.846a.75.75 0 01-1.442 0l-.813-2.846a3.75 3.75 0 00-2.576-2.576l-2.846-.813a.75.75 0 010-1.442l2.846-.813A3.75 3.75 0 007.466 7.89l.813-2.846A.75.75 0 019 4.5zM18 1.5a.75.75 0 01.728.568l.258 1.036c.236.94.97 1.674 1.91 1.91l1.036.258a.75.75 0 010 1.456l-1.036.258c-.94.236-1.674.97-1.91 1.91l-.258 1.036a.75.75 0 01-1.456 0l-.258-1.036a2.625 2.625 0 00-1.91-1.91l-1.036-.258a.75.75 0 010-1.456l1.036-.258a2.625 2.625 0 001.91-1.91l.258-1.036A.75.75 0 0118 1.5z" clipRule="evenodd" /></svg>
                              Enhance with AI
                            </button>
                            <button type="button" className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold border border-[#78a530]/40 text-[#78a530] hover:bg-[#78a530]/10 transition">
                              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
                              Approve
                            </button>
                            <button type="button" className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-[#2563eb] text-white hover:bg-[#1d4ed8] transition shadow-sm">
                              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" /></svg>
                              Send Report
                            </button>
                          </div>
                        </div>

                      </div>
                    )}
                  </div>
            </div>
          )}

          {/* Pulse Content */}
          {activeNav === "Pulse" && (
            <div className="h-full flex flex-col" style={{ zIndex: 1 }}>
              {/* Floating Tab Menu */}
              <FloatingTabMenu
                isDarkMode={isDarkMode}
                containerClassName="mb-4 flex items-center justify-between gap-3 flex-shrink-0"
                rightActions={
                  activePulseTab === "Work Item"
                    ? (
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setPulseWorkItemRefreshSignal((prev) => prev + 1)}
                          disabled={pulseWorkItemRefreshing}
                          className={`inline-flex items-center justify-center rounded-full w-8 h-8 border transition ${
                            isDarkMode
                              ? "bg-[#78a530]/10 text-[#78a530] border-[#78a530]/30 hover:bg-[#78a530]/20"
                              : "bg-[#f7fbee] text-[#5f8724] border-[#78a530]/30 hover:bg-[#e9f3d4]"
                          } ${pulseWorkItemRefreshing ? "opacity-80 cursor-not-allowed" : ""}`}
                          title={pulseWorkItemRefreshing ? "Refreshing Work Item data..." : "Refresh Work Item data"}
                          aria-label={pulseWorkItemRefreshing ? "Refreshing Work Item data" : "Refresh Work Item data"}
                        >
                          <svg className={`w-4 h-4 ${pulseWorkItemRefreshing ? "animate-spin" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v6h6M20 20v-6h-6M20 9a8 8 0 0 0-14.9-2M4 15a8 8 0 0 0 14.9 2" />
                          </svg>
                        </button>
                        {(pulseRecCounts.workItem + pulseRecCounts.bestPractice) > 0 && (
                          <button
                            type="button"
                            onClick={() => setPulseRecPanelOpen((prev) => !prev)}
                            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold border transition ${
                              pulseRecPanelOpen
                                ? "bg-[#78a530] text-white border-[#78a530]"
                                : isDarkMode
                                  ? "bg-[#78a530]/10 text-[#78a530] border-[#78a530]/30 hover:bg-[#78a530]/20"
                                  : "bg-[#f7fbee] text-[#5f8724] border-[#78a530]/30 hover:bg-[#e9f3d4]"
                            }`}
                          >
                            Recommendations
                            <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold leading-none ${pulseRecPanelOpen ? "bg-white/20 text-white" : "bg-[#78a530] text-white"}`}>
                              {pulseRecCounts.workItem + pulseRecCounts.bestPractice}
                            </span>
                          </button>
                        )}
                      </div>
                    )
                    : undefined
                }
              >
                <FloatingTabButton
                  isDarkMode={isDarkMode}
                  active={activePulseTab === "Action Items"}
                  onClick={() => { setActivePulseTab("Action Items"); setPulseRecPanelOpen(false) }}
                >
                  Action Items
                </FloatingTabButton>
                <FloatingTabButton
                  isDarkMode={isDarkMode}
                  active={activePulseTab === "Work Item"}
                  onClick={() => setActivePulseTab("Work Item")}
                >
                  Work Item
                </FloatingTabButton>
                <FloatingTabButton
                  isDarkMode={isDarkMode}
                  active={activePulseTab === "Blind Spots"}
                  onClick={() => { setActivePulseTab("Blind Spots"); setPulseRecPanelOpen(false) }}
                >
                  Blind Spots
                </FloatingTabButton>
                <FloatingTabButton
                  isDarkMode={isDarkMode}
                  active={activePulseTab === "Cadence Summary"}
                  onClick={() => { setActivePulseTab("Cadence Summary"); setPulseRecPanelOpen(false) }}
                >
                  Cadence Summary
                </FloatingTabButton>
                <FloatingTabButton
                  isDarkMode={isDarkMode}
                  active={activePulseTab === "Meeting Insights"}
                  onClick={() => { setActivePulseTab("Meeting Insights"); setPulseRecPanelOpen(false) }}
                >
                  Meeting Insights
                </FloatingTabButton>
              </FloatingTabMenu>

              {/* Main Content Area */}
              <div className="flex-1 min-h-0 overflow-y-auto pr-1">

                {/* PROJECT HEALTH */}
                {activePulseTab === "Work Item" && selectedProject && (
                  <ProjectHealthHierarchy
                    projectId={selectedProject.project_id}
                    mongoId={selectedProject._id}
                    backendUrl={runtimeBackendUrl}
                    refreshSignal={pulseWorkItemRefreshSignal}
                    onRefreshStateChange={setPulseWorkItemRefreshing}
                    initialHealthData={healthHierarchy}
                    fallbackHealthStatus={pendingPulseHealthStatus}
                    onConsumeFallbackHealthStatus={() => setPendingPulseHealthStatus(null)}
                    recommendationByTaskKey={recommendationByTaskKey}
                    recommendationAlerts={effectiveAlerts}
                    isDarkMode={isDarkMode}
                    recPanelOpen={pulseRecPanelOpen}
                    onRecPanelClose={() => setPulseRecPanelOpen(false)}
                    onRecommendationCountChange={setPulseRecCounts}
                  />
                )}

                {activePulseTab === "Action Items" && (
                  <div className="space-y-4 pb-6">
                    <div className="flex items-center justify-between">
                      <button
                        type="button"
                        onClick={openAddActionItemModal}
                        className="inline-flex items-center gap-1.5 rounded-md bg-[#78a530] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#6a9129]"
                      >
                        <span className="text-sm leading-none">+</span>
                        Add
                      </button>
                      <div className={`flex items-center gap-1 rounded-md border ${dm ? "bg-[#080d1a] border-white/[0.08]" : "bg-white border-gray-200"} p-1`}>
                        {[
                          { key: "open", label: "Open" },
                          { key: "closed", label: "Closed" },
                          { key: "cancelled", label: "Cancelled" },
                          { key: "archive", label: "Archive" },
                        ].map((view) => (
                          <button
                            key={view.key}
                            type="button"
                            onClick={() => setPulseActionItemView(view.key)}
                            className={`rounded px-2.5 py-1 text-xs font-medium transition ${
                              pulseActionItemView === view.key
                                ? "bg-[#78a530] text-white"
                                : dm ? "text-gray-400 hover:bg-white/[0.06]" : "text-gray-600 hover:bg-gray-100"
                            }`}
                          >
                            {view.label}
                          </button>
                        ))}
                      </div>
                    </div>

                    {pulseActionItemsLoading ? (
                      <div className={`${dm ? "bg-[#080d1a]" : "bg-white border-gray-200"} rounded-xl border shadow-sm p-6`} style={cardStyle}>
                        <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>Loading action items...</p>
                      </div>
                    ) : pulseActionItemsError ? (
                      <div className={`${dm ? "bg-[#080d1a]" : "bg-white border-red-200"} rounded-xl border shadow-sm p-6`} style={cardStyle}>
                        <p className="text-sm text-red-600">{pulseActionItemsError}</p>
                      </div>
                    ) : filteredPulseActionItems.length === 0 ? (
                      <div className={`${dm ? "bg-[#080d1a]" : "bg-white border-gray-200"} rounded-xl border shadow-sm p-6`} style={cardStyle}>
                        <p className="text-sm text-gray-400 italic">No action items for anyone.</p>
                      </div>
                    ) : (
                      <div className="space-y-5">
                        {(canViewAllActionItems
                          ? groupedPulseActionItems
                          : [{
                              ownerId: "self",
                              ownerLabel: "My Action Items",
                              items: filteredPulseActionItems,
                            }]
                        ).map((group) => (
                          <div key={group.ownerId} className={`rounded-xl border ${dm ? "bg-[#080d1a]" : "bg-white border-gray-200"} shadow-sm p-4`} style={cardStyle}>
                            {canViewAllActionItems && (
                              <div className="flex items-center justify-between mb-3">
                                <h4 className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>{group.ownerLabel}</h4>
                                <span className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>{group.items.length} notes</span>
                              </div>
                            )}
                            <div className={`flex flex-wrap items-start gap-4 ${canViewAllActionItems ? "max-h-[520px] overflow-y-auto pr-1" : ""}`}>
                              {group.items.map((item: any, index: number) => {
                                const actionItemId = String(item?.action_item_id || "")
                                const ownerId = String(item?.owner_user_id || group.ownerId || "unassigned")
                                const statusValue = String(item?.status || "").trim().toLowerCase()
                                const dueText = item?.due_date
                                  ? new Date(item.due_date).toLocaleDateString()
                                  : null
                                const closedOnText = item?.closed_at
                                  ? new Date(item.closed_at).toLocaleDateString()
                                  : null

                                const noteSeed = hashText(`${ownerId}:${item?.display_key || index}`)
                                const fallbackColor = ACTION_ITEM_NOTE_COLOR_CHOICES[noteSeed % ACTION_ITEM_NOTE_COLOR_CHOICES.length]
                                const selectedColor = String(item?.note_color || "").trim()
                                const noteColor = ACTION_ITEM_NOTE_COLOR_CHOICES.includes(selectedColor) ? selectedColor : fallbackColor
                                const noteSize = 208
                                const noteHeight = 188
                                const isOpen = statusValue === "open"
                                const isColorPickerOpen = activeActionItemColorPickerId === actionItemId

                                return (
                                  <div
                                    key={String(item?.action_item_id || item?.display_key || index)}
                                    style={{
                                      width: `${noteSize}px`,
                                      height: `${noteHeight}px`,
                                    }}
                                  >
                                  <div
                                    className="relative flex flex-col h-full w-full p-4"
                                    style={{
                                      backgroundColor: noteColor,
                                      border: "1px solid rgba(0,0,0,0.05)",
                                      boxShadow: "0 1px 1px rgba(0,0,0,0.11), -1px 2px 2px rgba(0,0,0,0.11), -2px 4px 4px rgba(0,0,0,0.11), -4px 8px 8px rgba(0,0,0,0.11), -8px 16px 16px rgba(0,0,0,0.11)",
                                    }}
                                  >
                                    <div
                                      className="absolute left-0 right-0 top-[10px] flex h-[16px] items-center justify-between px-3 text-[12px] text-[#2a2a2a]"
                                      style={{ fontFamily: "'Segoe Print', 'Bradley Hand', 'Comic Sans MS', cursive" }}
                                    >
                                      <span>{String(item?.display_key || "ACT-?")}</span>
                                      <span>{statusValue}</span>
                                    </div>
                                    <p
                                      title={String(item?.title || "Untitled action item")}
                                      className={`mt-7 ml-0.5 mb-14 text-[15px] leading-[1.25] ${
                                        statusValue === "open"
                                          ? "text-[#1a1a1a]"
                                          : statusValue === "cancelled"
                                            ? "text-red-700 line-through decoration-red-600 decoration-2"
                                            : "text-[#2c2c2c] line-through decoration-2"
                                      }`}
                                      style={{
                                        fontFamily: "'Chalkboard SE', 'Marker Felt', 'Bradley Hand', 'Segoe Print', 'Comic Sans MS', cursive",
                                        letterSpacing: "0.1px",
                                        display: "-webkit-box",
                                        WebkitLineClamp: 4,
                                        WebkitBoxOrient: "vertical",
                                        overflow: "hidden",
                                        overflowWrap: "anywhere",
                                        wordBreak: "break-word",
                                      }}
                                    >
                                      {String(item?.title || "Untitled action item")}
                                    </p>

                                    <div className="absolute bottom-4 left-11 right-16">
                                      <div className="text-[12px] text-[#242424]">
                                        {isOpen && dueText && (
                                          <p style={{ fontFamily: "'Marker Felt', 'Bradley Hand', 'Segoe Print', 'Comic Sans MS', cursive" }}>due {dueText}</p>
                                        )}
                                        {!isOpen && closedOnText && (
                                          <p style={{ fontFamily: "'Marker Felt', 'Bradley Hand', 'Segoe Print', 'Comic Sans MS', cursive" }}>closed {closedOnText}</p>
                                        )}
                                      </div>
                                    </div>

                                    {actionItemId && (
                                      <>
                                        <div className="absolute bottom-4 left-4">
                                          <button
                                            type="button"
                                            title="Change note color"
                                            aria-label="Change note color"
                                            disabled={Boolean(actionItemMutationPending[actionItemId])}
                                            onClick={() => setActiveActionItemColorPickerId((current) => current === actionItemId ? null : actionItemId)}
                                            className="h-4 w-4 rounded-full border border-black/35 text-[14px] leading-none text-transparent hover:text-transparent disabled:opacity-40 disabled:cursor-not-allowed"
                                            style={{
                                              fontFamily: "'Marker Felt', 'Bradley Hand', 'Segoe Print', 'Comic Sans MS', cursive",
                                              background: "conic-gradient(from 30deg, #F4A79D 0 25%, #F2D15E 25% 50%, #7ED59D 50% 75%, #75B9E8 75% 100%)",
                                            }}
                                          >
                                            ◉
                                          </button>
                                          {isColorPickerOpen && (
                                            <div className="absolute bottom-6 left-0 z-20 w-[130px] rounded border border-black/10 bg-white/95 p-2 shadow-md">
                                              <div className="grid grid-cols-5 gap-1.5">
                                                {ACTION_ITEM_NOTE_COLOR_CHOICES.map((color) => (
                                                  <button
                                                    key={`${actionItemId}-${color}`}
                                                    type="button"
                                                    title="Select color"
                                                    aria-label="Select note color"
                                                    disabled={Boolean(actionItemMutationPending[actionItemId])}
                                                    onClick={() => handleUpdateActionItemColor(actionItemId, color)}
                                                    className={`h-5 w-5 rounded-full border ${noteColor === color ? "border-black/80" : "border-black/20"} disabled:opacity-40`}
                                                    style={{ backgroundColor: color }}
                                                  />
                                                ))}
                                              </div>
                                            </div>
                                          )}
                                        </div>
                                      <button
                                        type="button"
                                        title={isOpen ? "Mark done" : "Reopen"}
                                        aria-label={isOpen ? "Mark action item done" : "Reopen action item"}
                                        disabled={Boolean(actionItemMutationPending[actionItemId])}
                                        onClick={() => (
                                          isOpen
                                            ? handleCloseActionItem(actionItemId, "done", statusValue)
                                            : handleReopenActionItem(actionItemId, statusValue)
                                        )}
                                        className="absolute bottom-4 right-4 text-[16px] leading-none text-[#1f1f1f] hover:text-[#111] disabled:opacity-40 disabled:cursor-not-allowed"
                                        style={{ fontFamily: "'Marker Felt', 'Bradley Hand', 'Segoe Print', 'Comic Sans MS', cursive" }}
                                      >
                                        {isOpen ? String.fromCharCode(10003) : String.fromCharCode(8634)}
                                      </button>
                                      {isOpen && (
                                        <button
                                          type="button"
                                          title="Cancel action item"
                                          aria-label="Cancel action item"
                                          disabled={Boolean(actionItemMutationPending[actionItemId])}
                                          onClick={() => handleCloseActionItem(actionItemId, "cancelled", statusValue)}
                                          className="absolute bottom-4 right-10 text-[16px] leading-none text-[#1f1f1f] hover:text-[#111] disabled:opacity-40 disabled:cursor-not-allowed"
                                          style={{ fontFamily: "'Marker Felt', 'Bradley Hand', 'Segoe Print', 'Comic Sans MS', cursive" }}
                                        >
                                          ×
                                        </button>
                                      )}
                                      {canDeleteActionItems && (
                                        <button
                                          type="button"
                                          title="Delete action item"
                                          aria-label="Delete action item"
                                          disabled={Boolean(actionItemMutationPending[actionItemId])}
                                          onClick={() => handleDeleteActionItem(actionItemId, statusValue)}
                                          className="absolute bottom-4 right-16 text-[12px] leading-none text-[#1f1f1f] hover:text-[#111] disabled:opacity-40 disabled:cursor-not-allowed"
                                          style={{ fontFamily: "'Marker Felt', 'Bradley Hand', 'Segoe Print', 'Comic Sans MS', cursive" }}
                                        >
                                          <svg
                                            aria-hidden="true"
                                            viewBox="0 0 24 24"
                                            className="h-3.5 w-3.5"
                                            fill="none"
                                            stroke="currentColor"
                                            strokeWidth="2"
                                            strokeLinecap="round"
                                            strokeLinejoin="round"
                                          >
                                            <path d="M3 6h18" />
                                            <path d="M8 6V4h8v2" />
                                            <path d="M19 6l-1 14H6L5 6" />
                                            <path d="M10 11v6" />
                                            <path d="M14 11v6" />
                                          </svg>
                                        </button>
                                      )}
                                      </>
                                    )}
                                  </div>
                                  </div>
                                )
                              })}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* BLIND SPOTS */}
                {activePulseTab === "Blind Spots" && (
                  <div className="space-y-5 pb-6">

                    <PulseSection
                      id="silent"
                      title="Silent Tasks"
                      accentColor="#6b7280"
                      highlight={false}
                      count={silentTasks.length}
                      emptyText="No tasks have gone silent"
                      emptyIcon="."
                      description="Open tasks with no activity for 5+ days"
                      isDarkMode={dm}
                      surfaceStyle={cardStyle}
                      headerStyle={cardHeaderStyle}
                    >
                      {silentTasks.map((task) => (
                        <PulseTaskRow
                          key={task.task_key}
                          task={task}
                          badge={{ badge: dm ? "bg-slate-700/40 text-gray-200" : "bg-gray-100 text-gray-700", label: `Silent ${task.days_silent}d` }}
                          isDarkMode={dm}
                        />
                      ))}
                    </PulseSection>

                    <PulseSection
                      id="stale-reviews"
                      title="Stale Reviews"
                      accentColor="#0891b2"
                      highlight={false}
                      count={staleReviews.length}
                      emptyText="No reviews waiting"
                      emptyIcon="."
                      description="Tasks stuck in review or QA for 2+ days, blocking downstream work"
                      isDarkMode={dm}
                      surfaceStyle={cardStyle}
                      headerStyle={cardHeaderStyle}
                    >
                      {staleReviews.map((task) => (
                        <PulseTaskRow
                          key={task.task_key}
                          task={task}
                          badge={{ badge: dm ? "bg-cyan-900/40 text-cyan-200" : "bg-cyan-100 text-cyan-700", label: `Waiting ${task.days_waiting}d` }}
                          isDarkMode={dm}
                        />
                      ))}
                    </PulseSection>

                  </div>
                )}


                {activePulseTab === "Meeting Insights" && (
                  <div className="space-y-5 pb-6">

                    <div className="flex items-center justify-between">
                      <div>
                        <h2 className={`text-base font-semibold ${textPrimary}`}>Meeting Insights</h2>
                        <p className={`text-sm ${textSecondary} mt-0.5`}>Capture meeting minutes, extract important items and follow up till closure</p>
                      </div>
                    </div>

                    {meetingsLoading ? (
                      <div className="rounded-xl overflow-hidden" style={cardStyle}>
                        <div className="py-14 text-center">
                          <p className={`text-sm ${textSecondary}`}>Loading meetings...</p>
                        </div>
                      </div>
                    ) : meetingsError ? (
                      <div className="rounded-xl overflow-hidden" style={cardStyle}>
                        <div className="py-14 text-center">
                          <p className="text-sm text-red-600">{meetingsError}</p>
                        </div>
                      </div>
                    ) : meetings.length === 0 ? (
                      <div className="rounded-xl overflow-hidden" style={cardStyle}>
                        <div className="flex flex-col items-center justify-center text-center py-14 px-8">
                          <div className={`w-12 h-12 rounded-full flex items-center justify-center mb-4 ${dm ? "bg-white/[0.05]" : "bg-gray-100"}`}>
                            <svg className={`w-6 h-6 ${dm ? "text-gray-500" : "text-gray-400"}`} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
                            </svg>
                          </div>
                          <p className={`text-sm font-semibold mb-1.5 ${textPrimary}`}>No meetings recorded yet</p>
                          <p className={`text-sm ${textSecondary} max-w-sm`}>
                            Install the ProMarshal browser plugin and capture a meeting. Minutes, action items, and blockers will appear here automatically.
                          </p>
                        </div>
                      </div>
                    ) : (
                      <div className="grid grid-cols-12 gap-4">

                        {/* Left: meetings list */}
                        <div className="col-span-12 md:col-span-4 space-y-2 max-h-[70vh] overflow-y-auto pr-1">
                          {meetings.map((m: any) => {
                            const isSelected = m.session_id === selectedMeetingId
                            const durationMin = Math.round((m.duration_seconds || 0) / 60)
                            const attendeeCount = (m.attendees || []).length
                            const dateLabel = formatMeetingDate(m.started_at)
                            return (
                              <button
                                key={m.session_id}
                                type="button"
                                onClick={() => setSelectedMeetingId(m.session_id)}
                                className={`w-full text-left rounded-xl p-3 transition border ${
                                  isSelected
                                    ? "border-[#78a530]"
                                    : (dm ? "border-white/[0.08] hover:border-white/[0.15]" : "border-gray-200 hover:border-gray-300")
                                }`}
                                style={cardStyle}
                              >
                                <p className={`text-sm font-semibold ${textPrimary} line-clamp-2`}>{m.title || "Untitled meeting"}</p>
                                <p className={`text-xs ${textSecondary} mt-1`}>
                                  {dateLabel}{durationMin > 0 ? ` · ${durationMin} min` : ""}
                                </p>
                                <p className={`text-xs ${textSecondary} mt-0.5`}>
                                  {attendeeCount} attendee{attendeeCount === 1 ? "" : "s"}
                                </p>
                              </button>
                            )
                          })}
                        </div>

                        {/* Right: meeting detail */}
                        <div className="col-span-12 md:col-span-8">
                          {meetingDetailLoading ? (
                            <div className="rounded-xl overflow-hidden" style={cardStyle}>
                              <div className="py-14 text-center">
                                <p className={`text-sm ${textSecondary}`}>Loading meeting...</p>
                              </div>
                            </div>
                          ) : !meetingDetail ? (
                            <div className="rounded-xl overflow-hidden" style={cardStyle}>
                              <div className="py-14 text-center">
                                <p className={`text-sm ${textSecondary}`}>Select a meeting to see details.</p>
                              </div>
                            </div>
                          ) : (
                            <div className="rounded-xl overflow-hidden p-6 space-y-5" style={cardStyle}>
                              {/* Header */}
                              <div>
                                <h3 className={`text-lg font-semibold ${textPrimary}`}>{meetingDetail.title || "Untitled meeting"}</h3>
                                <p className={`text-sm ${textSecondary} mt-0.5`}>
                                  {formatMeetingDate(meetingDetail.started_at)}
                                  {meetingDetail.duration_seconds > 0 ? ` · ${Math.round(meetingDetail.duration_seconds / 60)} min` : ""}
                                  {selectedProject?.project_name ? ` · ${selectedProject.project_name}` : ""}
                                </p>
                              </div>

                              {/* Attendees */}
                              {(meetingDetail.attendees || []).length > 0 && (
                                <div>
                                  <p className={`text-xs font-semibold uppercase tracking-widest ${textSecondary} mb-2`}>Attendees</p>
                                  <div className="space-y-1.5">
                                    {meetingDetail.attendees.map((a: any) => {
                                      const totalSecs = meetingDetail.attendees.reduce((sum: number, x: any) => sum + (x.speaking_seconds || 0), 0)
                                      const pct = totalSecs > 0 ? Math.max(4, Math.round((a.speaking_seconds / totalSecs) * 100)) : 0
                                      const mins = Math.max(0, Math.round((a.speaking_seconds || 0) / 60))
                                      return (
                                        <div key={a.name} className="flex items-center gap-3">
                                          <span className={`text-sm ${textPrimary} w-32 truncate`}>{a.name}</span>
                                          <div className={`flex-1 h-2 rounded-full ${dm ? "bg-white/[0.06]" : "bg-gray-100"}`}>
                                            <div className="h-2 rounded-full" style={{ width: `${pct}%`, backgroundColor: "#78a530" }} />
                                          </div>
                                          <span className={`text-xs ${textSecondary} w-14 text-right`}>{mins} min</span>
                                        </div>
                                      )
                                    })}
                                  </div>
                                </div>
                              )}

                              {/* Discussed Points */}
                              <div>
                                <p className={`text-xs font-semibold uppercase tracking-widest ${textSecondary} mb-2`}>
                                  Discussed Points{(meetingDetail.discussed_points || []).length > 0 ? ` · ${meetingDetail.discussed_points.length} items` : ""}
                                </p>
                                {(meetingDetail.discussed_points || []).length === 0 ? (
                                  <div className={`rounded-lg p-4 text-sm ${textSecondary} ${dm ? "bg-white/[0.03]" : "bg-gray-50"}`}>
                                    No discussed points were extracted for this meeting.
                                  </div>
                                ) : (
                                  <div className="space-y-2">
                                    {meetingDetail.discussed_points.map((p: any, idx: number) => (
                                      <DiscussedPointCard key={idx} point={p} dm={dm} textPrimary={textPrimary} textSecondary={textSecondary} />
                                    ))}
                                  </div>
                                )}
                              </div>

                              {/* Download */}
                              <div className="pt-2">
                                <button
                                  type="button"
                                  onClick={async () => {
                                    const sid = selectedMeetingId
                                    const pid = selectedProject?.project_id || selectedProject?._id
                                    if (!sid || !pid) return
                                    try {
                                      const res = await backendFetch(
                                        `${runtimeBackendUrl}/api/integrations/meetings/transcript/${encodeURIComponent(sid)}.md?project_id=${encodeURIComponent(pid)}`,
                                        {}
                                      )
                                      if (!res.ok) throw new Error(`download_failed status=${res.status}`)
                                      const blob = await res.blob()
                                      const dispositionHeader = res.headers.get("Content-Disposition") || ""
                                      const filenameMatch = dispositionHeader.match(/filename="?([^"]+)"?/i)
                                      const filename = filenameMatch ? filenameMatch[1] : `Meeting-${sid.slice(0, 8)}.md`
                                      const url = window.URL.createObjectURL(blob)
                                      const a = document.createElement("a")
                                      a.href = url
                                      a.download = filename
                                      document.body.appendChild(a)
                                      a.click()
                                      a.remove()
                                      window.URL.revokeObjectURL(url)
                                    } catch (err) {
                                      console.error("Transcript download failed:", err)
                                    }
                                  }}
                                  className={`inline-flex items-center gap-2 text-sm font-medium ${dm ? "text-[#a8d060] hover:text-[#c0e07a]" : "text-[#5f8724] hover:text-[#78a530]"}`}
                                >
                                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                                  </svg>
                                  Download transcript (.md)
                                </button>
                              </div>
                            </div>
                          )}
                        </div>

                      </div>
                    )}

                  </div>
                )}

                {activePulseTab === "Cadence Summary" && (
                  <CadenceSummary projectId={selectedProject?._id || ''} isDarkMode={isDarkMode} />
                )}
              </div>
            </div>
          )}

          {/* Control Panel Content */}
          {
            canAccessControlPanel && activeNav === "Control Panel" && (
              <div className="max-w-7xl mx-auto">
                {/* Floating Tab Menu */}
                <FloatingTabMenu isDarkMode={isDarkMode}>
                  <FloatingTabButton isDarkMode={isDarkMode} active={activeControlPanelTab === "Manage Team"} onClick={() => setActiveControlPanelTab("Manage Team")}>
                    Manage Team
                  </FloatingTabButton>
                  <FloatingTabButton isDarkMode={isDarkMode} active={activeControlPanelTab === "Integrate Tools"} onClick={() => setActiveControlPanelTab("Integrate Tools")}>
                    Integrate Tools
                  </FloatingTabButton>
                  <FloatingTabButton isDarkMode={isDarkMode} active={activeControlPanelTab === "Project Settings"} onClick={() => setActiveControlPanelTab("Project Settings")}>
                    Project Settings
                  </FloatingTabButton>
                </FloatingTabMenu>

                {/* Manage Team Tab */}
                {activeControlPanelTab === "Manage Team" && (
                  <div>
                    {/* Header with Invite Button */}
                    <div className="flex justify-between items-center mb-4">
                      <h1 className={`text-xl font-semibold ${textPrimary}`}>Team Members</h1>
                      <button
                        onClick={() => {
                          setInviteEmail("")
                          setInviteJiraAccountId(null)
                          setInviteError("")
                          setShowInviteModal(true)
                        }}
                        className="px-4 py-2 bg-[#78a530] text-white rounded-lg text-base font-semibold hover:bg-[#6a9129] transition shadow-sm"
                      >
                        + Invite Member
                      </button>
                    </div>

                    {/* Team Members Table */}
                    {teamMembers.length > 0 ? (
                      <div className={`${cardBg} rounded-xl border ${dm ? 'border-white/[0.08]' : 'border-gray-300'} shadow-sm overflow-hidden`}>
                        <table className="w-full table-fixed">
                          <colgroup>
                            <col style={{ width: "21%" }} />
                            <col style={{ width: "35%" }} />
                            <col style={{ width: "14%" }} />
                            <col style={{ width: "30%" }} />
                          </colgroup>
                          <thead className={`${dm ? 'bg-[#192035] border-white/[0.06]' : 'bg-gray-50 border-gray-300'} border-b`}>
                            <tr>
                              <th className={`text-left px-4 py-3 text-sm font-semibold ${dm ? 'text-gray-300' : 'text-gray-700'}`}>Name</th>
                              <th className={`text-left px-4 py-3 text-sm font-semibold ${dm ? 'text-gray-300' : 'text-gray-700'}`}>Email ID</th>
                              <th className={`text-left px-4 py-3 text-sm font-semibold ${dm ? 'text-gray-300' : 'text-gray-700'}`}>Status</th>
                              <th className={`text-left px-4 py-3 text-sm font-semibold ${dm ? 'text-gray-300' : 'text-gray-700'}`}>Role</th>
                            </tr>
                          </thead>
                          <tbody>
                            {teamMembers.map((member, index) => (
                              <tr key={member.id} className={`border-b ${dm ? 'border-white/[0.05] hover:bg-white/[0.03]' : 'border-gray-100 hover:bg-gray-50'} transition ${index === teamMembers.length - 1 ? 'border-b-0' : ''}`}>
                                <td className={`px-4 py-3 text-base ${textPrimary}`}>{member.name}</td>
                                <td className={`px-4 py-3 text-base ${textSecondary}`}>{member.email}</td>
                                <td className="px-4 py-3">
                                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-sm font-medium ${member.status === "Active" || member.status === "active"
                                    ? dm ? "bg-[#78a530]/15 text-[#78a530]" : "bg-green-100 text-green-800"
                                    : member.status === "Invite Sent" || member.status === "pending"
                                      ? dm ? "bg-yellow-900/30 text-yellow-400" : "bg-yellow-100 text-yellow-800"
                                      : dm ? "bg-white/[0.08] text-gray-400" : "bg-gray-100 text-gray-800"
                                    }`}>
                                    {member.status}
                                  </span>
                                </td>
                                <td className="px-4 py-3">
                                  <div className="flex items-center justify-between gap-2 w-full min-w-0">
                                    {editingMemberRoleUserId === member.user_id ? (
                                      <>
                                        <select
                                          value={editingMemberRoleValue}
                                          onChange={(event) => setEditingMemberRoleValue(event.target.value as "owner" | "member")}
                                          disabled={updatingMemberRoleUserId === member.user_id}
                                          className={`w-20 sm:w-24 rounded-md border px-2 py-1 text-sm ${dm ? "bg-[#111520] border-white/[0.12] text-gray-100" : "bg-white border-gray-300 text-gray-800"}`}
                                        >
                                          <option value="owner">Owner</option>
                                          <option value="member">Member</option>
                                        </select>
                                        <div className="w-14 sm:w-16 flex-shrink-0 flex items-center justify-end gap-1 sm:gap-2">
                                          <button
                                            type="button"
                                            onClick={handleSaveMemberRole}
                                            disabled={updatingMemberRoleUserId === member.user_id}
                                            className={`rounded-md p-1.5 transition ${dm ? "text-green-300 hover:bg-green-500/20" : "text-green-700 hover:bg-green-100"} disabled:opacity-50`}
                                            title="Save role"
                                            aria-label="Save role"
                                          >
                                            <CheckCircle2 className="w-4 h-4" />
                                          </button>
                                          <button
                                            type="button"
                                            onClick={handleCancelEditMemberRole}
                                            disabled={updatingMemberRoleUserId === member.user_id}
                                            className={`rounded-md p-1.5 transition ${dm ? "text-red-300 hover:bg-red-500/20" : "text-red-700 hover:bg-red-100"} disabled:opacity-50`}
                                            title="Cancel edit"
                                            aria-label="Cancel edit"
                                          >
                                            <XCircle className="w-4 h-4" />
                                          </button>
                                        </div>
                                      </>
                                    ) : (
                                      <>
                                        <span className={`text-base ${textPrimary} font-medium flex-1 min-w-0 truncate pr-1`}>{member.role}</span>
                                        <div className="flex-shrink-0 flex items-center justify-end gap-1">
                                          <button
                                            type="button"
                                            onClick={() => handleStartEditMemberRole(member)}
                                            disabled={!member.user_id || String(member?.status_raw || "").trim().toLowerCase() !== "active"}
                                            className={`rounded-md p-1.5 transition disabled:opacity-40 disabled:cursor-not-allowed ${dm ? "text-gray-300 hover:bg-white/10" : "text-gray-600 hover:bg-gray-100"}`}
                                            title="Edit role"
                                            aria-label="Edit role"
                                          >
                                            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                              <path strokeLinecap="round" strokeLinejoin="round" d="M12 20h9" />
                                              <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 3.5a2.121 2.121 0 113 3L7 19l-4 1 1-4 12.5-12.5z" />
                                            </svg>
                                          </button>
                                          {canRemoveMember(member) && (
                                            <button
                                              type="button"
                                              onClick={() => handleOpenRemoveMemberModal(member)}
                                              className={`rounded-md p-1.5 transition ${dm ? "text-red-300 hover:bg-red-500/20" : "text-red-700 hover:bg-red-100"}`}
                                              title="Remove member"
                                              aria-label="Remove member"
                                            >
                                              <Trash2 className="w-4 h-4" />
                                            </button>
                                          )}
                                        </div>
                                      </>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {memberRoleError && (
                          <div className={`px-4 py-3 text-sm ${dm ? "text-red-300" : "text-red-700"}`}>
                            {memberRoleError}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className={`${cardBg} rounded-xl border ${dm ? 'border-white/[0.08]' : 'border-gray-300'} shadow-sm p-8 text-center`}>
                        <div className="text-3xl mb-3">👥</div>
                        <h3 className={`text-lg font-semibold ${textPrimary} mb-1`}>No team members yet</h3>
                        <p className={`text-base ${textSecondary} mb-4`}>Invite your first team member to get started</p>
                        <button
                          onClick={() => {
                            setInviteEmail("")
                            setInviteJiraAccountId(null)
                            setInviteError("")
                            setShowInviteModal(true)
                          }}
                          className="px-4 py-2 bg-[#78a530] text-white rounded-lg text-base font-semibold hover:bg-[#6a9129] transition shadow-sm"
                        >
                          + Invite Member
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* Integrate Tools Tab */}
                {activeControlPanelTab === "Integrate Tools" && (
                  <div className="space-y-8">

                    {/* Communication Tools Section */}
                    <div>
                      <div className="flex items-center gap-3 mb-4">
                        <span className={`text-xs font-semibold tracking-widest uppercase ${dm ? "text-gray-400" : "text-gray-500"}`}>Communication Tools</span>
                        <div className={`flex-1 h-px ${dm ? "bg-white/[0.08]" : "bg-gray-200"}`} />
                      </div>
                      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                      {/* Slack Integration Card */}
                      {(() => {
                        const slackIntegration = getIntegration(selectedProject, "slack")
                        const isConnected = slackIntegration?.status === "connected" || !!slackIntegration

                        return (
                          <div
                            className={`flex flex-col items-center justify-between rounded-2xl border p-4 transition hover:shadow-lg ${dm ? 'backdrop-blur-md' : 'bg-white shadow-sm'}`}
                            style={{
                              background: dm
                                ? isConnected
                                  ? "linear-gradient(135deg, rgba(30,38,56,0.7) 0%, rgba(26,32,53,0.85) 100%)"
                                  : "linear-gradient(135deg, rgba(30,38,56,0.6) 0%, rgba(20,26,45,0.75) 100%)"
                                : undefined,
                              borderColor: isConnected ? "#78a530" : dm ? "rgba(255,255,255,0.1)" : "#e5e7eb",
                              boxShadow: isConnected
                                ? "0 8px 20px rgba(120,165,48,0.18)"
                                : dm ? "0 4px 24px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06)" : undefined,
                            }}
                          >
                            {/* Icon */}
                            <div className="flex flex-1 items-center justify-center mb-3">
                              <Image src="/logos/slack-logo.png" alt="Slack" width={64} height={64} style={{ objectFit: 'contain', width: 52, height: 52 }} />
                            </div>

                            {/* Content */}
                            <div className="flex flex-col items-center gap-2 mb-3">
                              <h3 className={`text-lg font-semibold ${textPrimary}`}>Slack</h3>
                              <p className={`text-sm ${textSecondary} text-center`}>
                                {isConnected
                                  ? `Connected to ${slackIntegration.team_name}`
                                  : "Connect your Slack workspace"}
                              </p>
                              {isConnected && (
                                <span
                                  className="inline-flex items-center rounded-full px-2 py-0.5 text-sm font-semibold"
                                  style={{ backgroundColor: "rgba(120,165,48,0.12)", color: "#78a530" }}
                                >
                                  ✓ Connected
                                </span>
                              )}
                              {isConnected && (
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    handleConfigureChannels()
                                  }}
                                  className="text-sm text-[#78a530] hover:underline font-medium"
                                >
                                  {slackIntegration.channels_configured ? "⚙️ Reconfigure Channels" : "⚙️ Configure Channels"}
                                </button>
                              )}
                            </div>

                            {isConnected && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleSyncSlack()
                                }}
                                disabled={isSyncingSlack || slackSyncSuccess}
                                className={`w-full mb-2 rounded-lg px-4 py-2 text-sm font-medium border-2 transition disabled:cursor-not-allowed ${slackSyncSuccess
                                  ? "bg-[#78a530] text-white border-[#78a530]"
                                  : "border-[#78a530] text-[#78a530] hover:bg-[#78a530] hover:text-white disabled:opacity-50"
                                  }`}
                              >
                                {isSyncingSlack ? (
                                  <span className="flex items-center justify-center gap-2">
                                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                    </svg>
                                    Syncing...
                                  </span>
                                ) : slackSyncSuccess ? (
                                  <span className="flex items-center justify-center gap-2">
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                    </svg>
                                    Synced
                                  </span>
                                ) : (
                                  <span className="flex items-center justify-center gap-2">
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                    </svg>
                                    Sync Now
                                  </span>
                                )}
                              </button>
                            )}

                            <button
                              onClick={() => {
                                if (isConnected) {
                                  handleDisconnectTool("slack")
                                } else {
                                  handleConnectTool("slack")
                                }
                              }}
                              disabled={isDisconnectingSlack || isConnectingSlack}
                              className={`w-full rounded-lg px-4 py-2 text-base font-semibold transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed ${
                                isConnected
                                  ? "bg-[#78a530] text-white hover:bg-[#6a9129]"
                                  : dm
                                    ? "border-2 border-[#5b8ec4] text-[#5b8ec4] hover:bg-[#1e2e45] hover:text-[#5b8ec4]"
                                    : "bg-[#3a5a8c] text-white hover:bg-[#2e4a78]"
                              }`}
                            >
                              {isDisconnectingSlack ? (
                                <span className="flex items-center justify-center gap-2">
                                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                  </svg>
                                  Disconnecting...
                                </span>
                              ) : isConnectingSlack ? (
                                <span className="flex items-center justify-center gap-2">
                                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                  </svg>
                                  Connecting...
                                </span>
                              ) : isConnected ? "Disconnect" : "Connect"}
                            </button>
                          </div>
                        )
                      })()}

                      {/* Google Meet - Browser Extension */}
                      {/* TODO: replace href with the actual Chrome Web Store URL after the extension is approved */}
                      <a
                        href="https://chromewebstore.google.com/"
                        target="_blank"
                        rel="noopener noreferrer"
                        className={`flex flex-col items-center justify-between rounded-2xl border p-4 transition hover:shadow-lg no-underline ${dm ? 'backdrop-blur-md' : 'bg-white shadow-sm'}`}
                        style={{
                          background: dm
                            ? "linear-gradient(135deg, rgba(30,38,56,0.6) 0%, rgba(20,26,45,0.75) 100%)"
                            : undefined,
                          borderColor: dm ? "rgba(255,255,255,0.1)" : "#e5e7eb",
                          boxShadow: dm ? "0 4px 24px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06)" : undefined,
                        }}
                      >
                        <div className="flex flex-1 items-center justify-center mb-3">
                          <Image src="/logos/gmeet.png" alt="Google Meet" width={64} height={64} style={{ objectFit: 'contain', width: 52, height: 52 }} />
                        </div>
                        <div className="flex flex-col items-center gap-2 mb-3">
                          <h3 className={`text-lg font-semibold ${textPrimary}`}>Google Meet</h3>
                          <p className={`text-sm ${textSecondary} text-center`}>
                            Capture meeting transcripts with our browser plugin
                          </p>
                        </div>
                        <div
                          className={`w-full rounded-lg px-4 py-2 text-center text-base font-semibold transition shadow-sm ${
                            dm
                              ? "border-2 border-[#5b8ec4] text-[#5b8ec4] hover:bg-[#1e2e45]"
                              : "bg-[#3a5a8c] text-white hover:bg-[#2e4a78]"
                          }`}
                        >
                          Install Extension
                        </div>
                      </a>

                      {/* Teams - Coming Soon */}
                      <div
                        className={`flex flex-col items-center justify-between rounded-2xl border p-4 opacity-50 cursor-not-allowed select-none ${dm ? 'backdrop-blur-md' : 'bg-white shadow-sm'}`}
                        style={{
                          background: dm ? "linear-gradient(135deg, rgba(20,26,45,0.5) 0%, rgba(15,20,35,0.6) 100%)" : undefined,
                          borderColor: dm ? "rgba(255,255,255,0.07)" : "#e5e7eb",
                        }}
                      >
                        <div className="flex flex-1 items-center justify-center mb-3">
                          <Image src="/logos/teams.png" alt="Teams" width={64} height={64} style={{ objectFit: 'contain', width: 52, height: 52 }} />
                        </div>
                        <div className="flex flex-col items-center gap-2 mb-3">
                          <h3 className={`text-lg font-semibold ${textPrimary}`}>Teams</h3>
                          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${dm ? "bg-white/[0.08] text-gray-400" : "bg-gray-100 text-gray-500"}`}>
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
                            Coming Soon
                          </span>
                        </div>
                        <div className={`w-full rounded-lg px-4 py-2 text-center text-sm font-medium ${dm ? "bg-white/[0.05] text-gray-500" : "bg-gray-100 text-gray-400"}`}>
                          Not Available Yet
                        </div>
                      </div>
                    </div>{/* end communication grid */}
                    </div>{/* end communication section */}

                    {/* Work Item Tools Section */}
                    <div>
                      <div className="flex items-center gap-3 mb-4">
                        <span className={`text-xs font-semibold tracking-widest uppercase ${dm ? "text-gray-400" : "text-gray-500"}`}>Work Item Tools</span>
                        <div className={`flex-1 h-px ${dm ? "bg-white/[0.08]" : "bg-gray-200"}`} />
                      </div>
                      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">

                      {/* Jira Integration Card */}
                      {(() => {
                        const jiraIntegration = getIntegration(selectedProject, "jira")
                        const isConnected = jiraIntegration?.status === "connected"
                        const isPendingSelection = jiraIntegration?.status === "pending_project_selection"

                        return (
                          <div
                            className={`flex flex-col items-center justify-between rounded-2xl border p-4 transition hover:shadow-lg ${dm ? 'backdrop-blur-md' : 'bg-white shadow-sm'}`}
                            style={{
                              background: dm
                                ? isConnected
                                  ? "linear-gradient(135deg, rgba(30,38,56,0.7) 0%, rgba(26,32,53,0.85) 100%)"
                                  : "linear-gradient(135deg, rgba(30,38,56,0.6) 0%, rgba(20,26,45,0.75) 100%)"
                                : undefined,
                              borderColor: isConnected ? "#78a530" : isPendingSelection ? (dm ? "rgba(255,255,255,0.2)" : "#9ca3af") : (dm ? "rgba(255,255,255,0.1)" : "#e5e7eb"),
                              boxShadow: isConnected
                                ? "0 8px 20px rgba(120,165,48,0.18)"
                                : dm ? "0 4px 24px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06)" : undefined,
                            }}
                          >
                            {/* Icon */}
                            <div className="flex flex-1 items-center justify-center mb-3">
                              <Image src="/logos/jira.png" alt="Jira" width={64} height={64} style={{ objectFit: 'contain', width: 52, height: 52 }} />
                            </div>

                            {/* Content */}
                            <div className="flex flex-col items-center gap-2 mb-3">
                              <h3 className={`text-lg font-semibold ${textPrimary}`}>Jira</h3>
                              <p className={`text-sm ${textSecondary} text-center`}>
                                {isConnected
                                  ? `Connected to ${jiraIntegration.site_name}`
                                  : isPendingSelection
                                    ? "Select Jira project to continue"
                                    : "Connect your Jira site"}
                              </p>
                              {isConnected && (
                                <span
                                  className="inline-flex items-center rounded-full px-2 py-0.5 text-sm font-semibold"
                                  style={{ backgroundColor: "rgba(120,165,48,0.12)", color: "#78a530" }}
                                >
                                  Connected
                                </span>
                              )}
                              {isPendingSelection && (
                                <span
                                  className="inline-flex items-center rounded-full px-2 py-0.5 text-sm font-semibold"
                                  style={{ backgroundColor: "rgba(156,163,175,0.12)", color: "#6b7280" }}
                                >
                                  Action Required
                                </span>
                              )}
                            </div>

                            {/* Sync Now button - only show when connected */}
                            {isConnected && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleSyncJira()
                                }}
                                disabled={isSyncingJira || jiraSyncSuccess}
                                className={`w-full mb-2 rounded-lg px-4 py-2 text-sm font-medium border-2 transition disabled:cursor-not-allowed ${jiraSyncSuccess
                                  ? "bg-[#78a530] text-white border-[#78a530]"
                                  : "border-[#78a530] text-[#78a530] hover:bg-[#78a530] hover:text-white disabled:opacity-50"
                                  }`}
                              >
                                {isSyncingJira ? (
                                  <span className="flex items-center justify-center gap-2">
                                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                    </svg>
                                    Syncing...
                                  </span>
                                ) : jiraSyncSuccess ? (
                                  <span className="flex items-center justify-center gap-2">
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                    </svg>
                                    Synced
                                  </span>
                                ) : (
                                  <span className="flex items-center justify-center gap-2">
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                                    </svg>
                                    Sync Now
                                  </span>
                                )}
                              </button>
                            )}

                            {/* Error message display */}
                            {jiraSyncError && (
                              <div className={`mb-2 p-3 ${dm ? 'bg-red-900/30 border-red-800/50' : 'bg-red-50 border-red-200'} border rounded-lg`}>
                                <div className="flex items-start gap-2">
                                  <svg className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                  </svg>
                                  <div className="flex-1">
                                    <p className="text-sm font-medium text-red-800">Sync Failed</p>
                                    <p className="text-xs text-red-600 mt-1">{jiraSyncError}</p>
                                  </div>
                                  <button
                                    onClick={() => setJiraSyncError(null)}
                                    className="text-red-400 hover:text-red-600 flex-shrink-0"
                                  >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                    </svg>
                                  </button>
                                </div>
                              </div>
                            )}

                            <button
                              onClick={() => {
                                if (isPendingSelection) {
                                  setJiraSelectionProjectId(selectedProject?._id || "")
                                  setShowJiraProjectSelectionModal(true)
                                } else if (isConnected) {
                                  handleDisconnectTool("jira")
                                } else {
                                  handleConnectTool("jira")
                                }
                              }}
                              disabled={isDisconnectingJira || isConnectingJira || isLoadingJiraProjectSelection}
                              className={`w-full rounded-lg px-4 py-2 text-base font-semibold transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed ${
                                isConnected
                                  ? "bg-[#78a530] text-white hover:bg-[#6a9129]"
                                  : isPendingSelection
                                    ? dm ? "border-2 border-amber-400 text-amber-300 hover:bg-amber-500 hover:text-white hover:border-amber-500" : "bg-amber-500 text-white hover:bg-amber-600"
                                    : dm
                                      ? "border-2 border-[#5b8ec4] text-[#5b8ec4] hover:bg-[#1e2e45] hover:text-[#5b8ec4]"
                                      : "bg-[#3a5a8c] text-white hover:bg-[#2e4a78]"
                              }`}
                            >
                              {isDisconnectingJira ? (
                                <span className="flex items-center justify-center gap-2">
                                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                  </svg>
                                  Disconnecting...
                                </span>
                              ) : isConnectingJira ? (
                                <span className="flex items-center justify-center gap-2">
                                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                  </svg>
                                  Connecting...
                                </span>
                              ) : isLoadingJiraProjectSelection ? (
                                <span className="flex items-center justify-center gap-2">
                                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                  </svg>
                                  Loading...
                                </span>
                              ) : isPendingSelection ? "Select Project" : isConnected ? "Disconnect" : "Connect"}
                            </button>
                          </div>
                        )
                      })()}

                      {/* Asana - Coming Soon */}
                      <div
                        className={`flex flex-col items-center justify-between rounded-2xl border p-4 opacity-50 cursor-not-allowed select-none ${dm ? 'backdrop-blur-md' : 'bg-white shadow-sm'}`}
                        style={{
                          background: dm ? "linear-gradient(135deg, rgba(20,26,45,0.5) 0%, rgba(15,20,35,0.6) 100%)" : undefined,
                          borderColor: dm ? "rgba(255,255,255,0.07)" : "#e5e7eb",
                        }}
                      >
                        <div className="flex flex-1 items-center justify-center mb-3">
                          <Image src="/logos/asana.png" alt="Asana" width={64} height={64} style={{ objectFit: 'contain', width: 52, height: 52 }} />
                        </div>
                        <div className="flex flex-col items-center gap-2 mb-3">
                          <h3 className={`text-lg font-semibold ${textPrimary}`}>Asana</h3>
                          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${dm ? "bg-white/[0.08] text-gray-400" : "bg-gray-100 text-gray-500"}`}>
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
                            Coming Soon
                          </span>
                        </div>
                        <div className={`w-full rounded-lg px-4 py-2 text-center text-sm font-medium ${dm ? "bg-white/[0.05] text-gray-500" : "bg-gray-100 text-gray-400"}`}>
                          Not Available Yet
                        </div>
                      </div>

                      {/* Linear - Coming Soon */}
                      <div
                        className={`flex flex-col items-center justify-between rounded-2xl border p-4 opacity-50 cursor-not-allowed select-none ${dm ? 'backdrop-blur-md' : 'bg-white shadow-sm'}`}
                        style={{
                          background: dm ? "linear-gradient(135deg, rgba(20,26,45,0.5) 0%, rgba(15,20,35,0.6) 100%)" : undefined,
                          borderColor: dm ? "rgba(255,255,255,0.07)" : "#e5e7eb",
                        }}
                      >
                        <div className="flex flex-1 items-center justify-center mb-3">
                          <Image src="/logos/linear.png" alt="Linear" width={64} height={64} style={{ objectFit: 'contain', width: 52, height: 52 }} />
                        </div>
                        <div className="flex flex-col items-center gap-2 mb-3">
                          <h3 className={`text-lg font-semibold ${textPrimary}`}>Linear</h3>
                          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${dm ? "bg-white/[0.08] text-gray-400" : "bg-gray-100 text-gray-500"}`}>
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
                            Coming Soon
                          </span>
                        </div>
                        <div className={`w-full rounded-lg px-4 py-2 text-center text-sm font-medium ${dm ? "bg-white/[0.05] text-gray-500" : "bg-gray-100 text-gray-400"}`}>
                          Not Available Yet
                        </div>
                      </div>

                      {/* ClickUp - Coming Soon */}
                      <div
                        className={`flex flex-col items-center justify-between rounded-2xl border p-4 opacity-50 cursor-not-allowed select-none ${dm ? 'backdrop-blur-md' : 'bg-white shadow-sm'}`}
                        style={{
                          background: dm ? "linear-gradient(135deg, rgba(20,26,45,0.5) 0%, rgba(15,20,35,0.6) 100%)" : undefined,
                          borderColor: dm ? "rgba(255,255,255,0.07)" : "#e5e7eb",
                        }}
                      >
                        <div className="flex flex-1 items-center justify-center mb-3">
                          <Image src="/logos/clickup.png" alt="ClickUp" width={64} height={64} style={{ objectFit: 'contain', width: 52, height: 52 }} />
                        </div>
                        <div className="flex flex-col items-center gap-2 mb-3">
                          <h3 className={`text-lg font-semibold ${textPrimary}`}>ClickUp</h3>
                          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${dm ? "bg-white/[0.08] text-gray-400" : "bg-gray-100 text-gray-500"}`}>
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
                            Coming Soon
                          </span>
                        </div>
                        <div className={`w-full rounded-lg px-4 py-2 text-center text-sm font-medium ${dm ? "bg-white/[0.05] text-gray-500" : "bg-gray-100 text-gray-400"}`}>
                          Not Available Yet
                        </div>
                      </div>

                      </div>{/* end work item grid */}
                    </div>{/* end work item section */}
                  </div>
                )}

                {/* Project Settings Tab */}
                {activeControlPanelTab === "Project Settings" && (
                  <div>
                    <h1 className={`text-xl font-semibold mb-6 ${textPrimary}`}>Project Settings</h1>
                    {settingsSaveSuccess && (
                      <div
                        className={`fixed bottom-6 right-6 z-50 rounded-lg border px-4 py-3 text-sm font-semibold shadow-lg ${
                          dm
                            ? "border-[#78a530]/40 bg-[#101a0b] text-[#b7dd80]"
                            : "border-[#78a530]/30 bg-[#f6fbe9] text-[#4b6a1b]"
                        }`}
                        role="status"
                        aria-live="polite"
                      >
                        Project settings saved successfully.
                      </div>
                    )}

                    {selectedProject && (
                      <div className="max-w-6xl">
                        <div className={`${dm ? "border-white/[0.08]" : "border-gray-300"} border-t mt-2`}>
                          {/* Project Timezone Row */}
                          <div className={`py-9 border-b ${dm ? "border-white/[0.08]" : "border-gray-300"}`}>
                            <div className="grid gap-12 md:grid-cols-[minmax(0,1.2fr)_minmax(300px,400px)] md:items-start">
                              <div>
                                <h3 className={`text-base font-semibold ${textPrimary}`}>Project Timezone</h3>
                                <p className={`text-sm ${textSecondary} mt-1`}>
                                  Set the timezone for this project to schedule reminders correctly
                                </p>
                              </div>
                              <div className="md:pr-6">
                                <label className={`block text-sm font-medium ${dm ? 'text-gray-300' : 'text-gray-700'} mb-2`}>
                                  Timezone
                                </label>
                                <TimezoneDropdown
                                  value={settingsTimezone}
                                  options={availableTimezones.length > 0 ? availableTimezones : [settingsTimezone || "UTC"]}
                                  onChange={(nextTimezone) => {
                                    setSettingsTimezone(nextTimezone)
                                    setHasUnsavedSettings(true)
                                  }}
                                  isDarkMode={dm}
                                  className="w-full"
                                />
                              </div>
                            </div>
                          </div>

                          {/* Cadence Row */}
                          <div className={`py-10 border-b ${dm ? "border-white/[0.08]" : "border-gray-300"}`}>
                            <div className="grid gap-12 md:grid-cols-[minmax(0,1.2fr)_minmax(300px,400px)] md:items-start">
                              <div>
                                <h3 className={`text-lg font-semibold ${textPrimary}`}>Cadence</h3>
                                <p className={`text-base ${textSecondary} mt-1`}>
                                  Configure when to send daily cadence reminders to your team
                                </p>
                                {settingsReminderEnabled && (
                                  <div className="mt-8 flex flex-wrap items-center gap-x-7 gap-y-3">
                                    <label className={`inline-flex items-center gap-2 text-sm ${dm ? "text-gray-300" : "text-gray-700"}`}>
                                      <input
                                        type="checkbox"
                                        checked={settingsReminderSkipWeekends}
                                        onChange={(event) => {
                                          setSettingsReminderSkipWeekends(event.target.checked)
                                          setHasUnsavedSettings(true)
                                        }}
                                        className="h-4 w-4 rounded border-gray-300 text-[#78a530] focus:ring-[#78a530]"
                                      />
                                      Skip weekends
                                    </label>
                                    <label className={`inline-flex items-center gap-2 text-sm ${dm ? "text-gray-300" : "text-gray-700"}`}>
                                      <input
                                        type="checkbox"
                                        checked={settingsReminderIgnoreFollowupForOwner}
                                        onChange={(event) => {
                                          setSettingsReminderIgnoreFollowupForOwner(event.target.checked)
                                          setHasUnsavedSettings(true)
                                        }}
                                        className="h-4 w-4 rounded border-gray-300 text-[#78a530] focus:ring-[#78a530]"
                                      />
                                      Ignore follow-up for owner
                                    </label>
                                  </div>
                                )}
                              </div>
                              <div className="space-y-4 md:pr-6">
                                <div className="flex justify-end">
                                  <label className="relative inline-flex items-center cursor-pointer">
                                    <input
                                      type="checkbox"
                                      checked={settingsReminderEnabled}
                                      onChange={(e) => {
                                        setSettingsReminderEnabled(e.target.checked)
                                        setHasUnsavedSettings(true)
                                      }}
                                      className="sr-only peer"
                                    />
                                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-[#78a530]/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[#78a530]"></div>
                                  </label>
                                </div>
                                {settingsReminderEnabled && (
                                  <div className="space-y-3">
                                    <div>
                                      <label className={`block text-base font-medium ${dm ? 'text-gray-300' : 'text-gray-700'} mb-2`}>
                                        Cadence Time
                                      </label>
                                      <div className="flex items-center gap-3">
                                        <CompactNumberDropdown
                                          value={splitScheduleTime(settingsReminderTime, "09:00").hour}
                                          options={SCHEDULE_HOUR_OPTIONS}
                                          dm={dm}
                                          ariaLabel="Cadence hour"
                                          onChange={(nextHour) => {
                                            const parts = splitScheduleTime(settingsReminderTime, "09:00")
                                            setSettingsReminderTime(composeScheduleTime(nextHour, parts.minute))
                                            setHasUnsavedSettings(true)
                                          }}
                                        />
                                        <CompactNumberDropdown
                                          value={splitScheduleTime(settingsReminderTime, "09:00").minute}
                                          options={SCHEDULE_MINUTE_OPTIONS}
                                          dm={dm}
                                          ariaLabel="Cadence minute"
                                          onChange={(nextMinute) => {
                                            const parts = splitScheduleTime(settingsReminderTime, "09:00")
                                            setSettingsReminderTime(composeScheduleTime(parts.hour, nextMinute))
                                            setHasUnsavedSettings(true)
                                          }}
                                        />
                                      </div>
                                      <p className={`text-sm ${textMuted} mt-1`}>
                                        Daily cadence reminders will be sent at this time in {settingsTimezone}
                                      </p>
                                    </div>
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>

                          {/* Action Item Digest Row */}
                          <div className={`py-10 border-b ${dm ? "border-white/[0.08]" : "border-gray-300"}`}>
                            <div className="grid gap-12 md:grid-cols-[minmax(0,1.2fr)_minmax(300px,400px)] md:items-start">
                              <div>
                                <h3 className={`text-lg font-semibold ${textPrimary}`}>Action Item Digest</h3>
                                <p className={`text-base ${textSecondary} mt-1`}>
                                  Configure when to send daily action item digest reminders
                                </p>
                                {settingsActionItemDigestEnabled && (
                                  <div className="mt-8 flex flex-wrap items-center gap-x-7 gap-y-3">
                                    <label className={`inline-flex items-center gap-2 text-sm ${dm ? "text-gray-300" : "text-gray-700"}`}>
                                      <input
                                        type="checkbox"
                                        checked={settingsActionItemDigestSkipWeekends}
                                        onChange={(event) => {
                                          setSettingsActionItemDigestSkipWeekends(event.target.checked)
                                          setHasUnsavedSettings(true)
                                        }}
                                        className="h-4 w-4 rounded border-gray-300 text-[#78a530] focus:ring-[#78a530]"
                                      />
                                      Skip weekends
                                    </label>
                                    <label className={`inline-flex items-center gap-2 text-sm ${dm ? "text-gray-300" : "text-gray-700"}`}>
                                      <input
                                        type="checkbox"
                                        checked={settingsActionItemDigestIgnoreFollowupForOwner}
                                        onChange={(event) => {
                                          setSettingsActionItemDigestIgnoreFollowupForOwner(event.target.checked)
                                          setHasUnsavedSettings(true)
                                        }}
                                        className="h-4 w-4 rounded border-gray-300 text-[#78a530] focus:ring-[#78a530]"
                                      />
                                      Ignore follow-up for owner
                                    </label>
                                  </div>
                                )}
                              </div>
                              <div className="space-y-4 md:pr-6">
                                <div className="flex justify-end">
                                  <label className="relative inline-flex items-center cursor-pointer">
                                    <input
                                      type="checkbox"
                                      checked={settingsActionItemDigestEnabled}
                                      onChange={(e) => {
                                        setSettingsActionItemDigestEnabled(e.target.checked)
                                        setHasUnsavedSettings(true)
                                      }}
                                      className="sr-only peer"
                                    />
                                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-[#78a530]/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[#78a530]"></div>
                                  </label>
                                </div>
                                {settingsActionItemDigestEnabled && (
                                  <div className="space-y-3">
                                    <div>
                                      <label className={`block text-base font-medium ${dm ? 'text-gray-300' : 'text-gray-700'} mb-2`}>
                                        Digest Time
                                      </label>
                                      <div className="flex items-center gap-3">
                                        <CompactNumberDropdown
                                          value={splitScheduleTime(settingsActionItemDigestTime, "12:30").hour}
                                          options={SCHEDULE_HOUR_OPTIONS}
                                          dm={dm}
                                          ariaLabel="Digest hour"
                                          onChange={(nextHour) => {
                                            const parts = splitScheduleTime(settingsActionItemDigestTime, "12:30")
                                            setSettingsActionItemDigestTime(composeScheduleTime(nextHour, parts.minute))
                                            setHasUnsavedSettings(true)
                                          }}
                                        />
                                        <CompactNumberDropdown
                                          value={splitScheduleTime(settingsActionItemDigestTime, "12:30").minute}
                                          options={SCHEDULE_MINUTE_OPTIONS}
                                          dm={dm}
                                          ariaLabel="Digest minute"
                                          onChange={(nextMinute) => {
                                            const parts = splitScheduleTime(settingsActionItemDigestTime, "12:30")
                                            setSettingsActionItemDigestTime(composeScheduleTime(parts.hour, nextMinute))
                                            setHasUnsavedSettings(true)
                                          }}
                                        />
                                      </div>
                                      <p className={`text-sm ${textMuted} mt-1`}>
                                        Daily digest reminders will be sent at this time in {settingsTimezone}
                                      </p>
                                    </div>
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                        </div>

                        {/* Save Button */}
                        <div className="flex items-center justify-end gap-3 pt-10">
                          <button
                            onClick={() => {
                              // Reset to original values
                              const project = selectedProject as any
                              setSettingsTimezone(project.timezone || "UTC")
                              setSettingsReminderEnabled(savedReminderEnabled)
                              setSettingsReminderTime(savedReminderTime)
                              setSettingsReminderSkipWeekends(savedReminderSkipWeekends)
                              setSettingsReminderIgnoreFollowupForOwner(savedReminderIgnoreFollowupForOwner)
                              setSettingsActionItemDigestEnabled(savedActionItemDigestEnabled)
                              setSettingsActionItemDigestTime(savedActionItemDigestTime)
                              setSettingsActionItemDigestSkipWeekends(savedActionItemDigestSkipWeekends)
                              setSettingsActionItemDigestIgnoreFollowupForOwner(savedActionItemDigestIgnoreFollowupForOwner)
                              setHasUnsavedSettings(false)
                            }}
                            disabled={!hasUnsavedSettings || isSavingSettings}
                            className={`px-6 py-2.5 border ${dm ? 'border-white/10 text-gray-300 hover:bg-white/5' : 'border-gray-300 text-gray-700 hover:bg-gray-50'} rounded-lg font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed`}
                          >
                            Cancel
                          </button>
                          <button
                            onClick={handleSaveSettings}
                            disabled={!hasUnsavedSettings || isSavingSettings}
                            className="px-6 py-2.5 bg-[#78a530] text-white rounded-lg font-semibold hover:bg-[#6a9129] transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                          >
                            {isSavingSettings ? (
                              <>
                                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                                Saving...
                              </>
                            ) : (
                              "Save Changes"
                            )}
                          </button>
                        </div>

                        {/* Danger Zone */}
                        <div className={`mt-10 border rounded-xl p-6 ${dm ? "border-red-500/40 bg-red-500/5" : "border-red-200 bg-red-50/50"}`}>
                          <div className="flex items-start justify-between gap-4">
                            <div>
                              <h3 className={`text-lg font-semibold ${dm ? "text-red-300" : "text-red-700"}`}>Danger Zone</h3>
                              <p className={`text-sm mt-1 ${dm ? "text-red-200/80" : "text-red-700/80"}`}>
                                Deleting this project permanently removes project data and cannot be undone.
                              </p>
                            </div>
                            <button
                              onClick={() => {
                                setDeleteProjectError(null)
                                setDeleteProjectConfirmText("")
                                setShowDeleteProjectModal(true)
                              }}
                              className={`px-4 py-2 rounded-lg font-semibold transition ${dm ? "bg-red-500/20 text-red-200 hover:bg-red-500/30 border border-red-500/40" : "bg-red-600 text-white hover:bg-red-700"}`}
                            >
                              Delete Project
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          }

          {/* Add Action Item Modal */}
          {
            showAddActionItemModal && (
              <>
                <div className="fixed inset-0 bg-black/50 z-40" onClick={closeAddActionItemModal} />
                <div className="fixed inset-0 flex items-center justify-center z-50 p-4">
                  <div
                    className="w-full max-w-md rounded-2xl p-5 shadow-2xl"
                    style={dm ? {
                      background: "linear-gradient(135deg, rgba(30,38,56,0.92) 0%, rgba(17,21,32,0.96) 100%)",
                      backdropFilter: "blur(16px)",
                      WebkitBackdropFilter: "blur(16px)",
                      border: "1px solid rgba(255,255,255,0.18)",
                      boxShadow: "0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.12)",
                    } : { background: "#fff", border: "1px solid #e5e7eb" }}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className={`text-base font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Add Action Item</h3>
                        <p className={`mt-0.5 text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>Create a quick reminder for a project member.</p>
                      </div>
                      <button
                        type="button"
                        onClick={closeAddActionItemModal}
                        disabled={addActionItemSubmitting}
                        className={`h-7 w-7 rounded-full border disabled:opacity-50 ${dm ? "border-white/[0.15] text-gray-300 hover:bg-white/[0.08]" : "border-gray-300 text-gray-600 hover:bg-gray-50"}`}
                        aria-label="Close add action item modal"
                      >
                        ×
                      </button>
                    </div>

                    <div className="mt-4 space-y-3">
                      <div>
                        <label className={`mb-1 block text-xs font-semibold ${dm ? "text-gray-300" : "text-gray-700"}`}>Action Item</label>
                        <input
                          type="text"
                          value={addActionItemTitle}
                          onChange={(event) => setAddActionItemTitle(event.target.value)}
                          placeholder="Enter action item"
                          className={`w-full rounded-md border px-3 py-2 text-sm focus:border-[#78a530] focus:outline-none focus:ring-1 focus:ring-[#78a530] ${dm ? "bg-[#111520] border-white/[0.12] text-gray-100 placeholder-gray-500" : "border-gray-300 text-gray-800"}`}
                        />
                      </div>

                      <div>
                        <label className={`mb-1 block text-xs font-semibold ${dm ? "text-gray-300" : "text-gray-700"}`}>Owner</label>
                        <select
                          value={addActionItemOwnerUserId}
                          onChange={(event) => setAddActionItemOwnerUserId(event.target.value)}
                          className={`w-full rounded-md border px-3 py-2 text-sm focus:border-[#78a530] focus:outline-none focus:ring-1 focus:ring-[#78a530] ${dm ? "bg-[#111520] border-white/[0.12] text-gray-100" : "border-gray-300 text-gray-800"}`}
                        >
                          <option value="">Select owner</option>
                          {activeProjectMembers.map((member: any) => {
                            const memberId = String(member?.id || "").trim()
                            if (!memberId) return null
                            const memberName = String(member?.name || memberId).trim()
                            return (
                              <option key={memberId} value={memberId}>
                                {memberName}
                              </option>
                            )
                          })}
                        </select>
                      </div>

                      <div>
                        <label className={`mb-1 block text-xs font-semibold ${dm ? "text-gray-300" : "text-gray-700"}`}>Due Date (Optional)</label>
                        <input
                          type="date"
                          value={addActionItemDueDate}
                          onChange={(event) => setAddActionItemDueDate(event.target.value)}
                          className={`w-full rounded-md border px-3 py-2 text-sm focus:border-[#78a530] focus:outline-none focus:ring-1 focus:ring-[#78a530] ${dm ? "bg-[#111520] border-white/[0.12] text-gray-100" : "border-gray-300 text-gray-800"}`}
                        />
                      </div>

                      <div>
                        <label className={`mb-1 block text-xs font-semibold ${dm ? "text-gray-300" : "text-gray-700"}`}>Note Color</label>
                        <div className="flex flex-wrap gap-2">
                          {ACTION_ITEM_NOTE_COLOR_CHOICES.map((color) => {
                            const selected = addActionItemColor === color
                            return (
                              <button
                                key={color}
                                type="button"
                                title={color}
                                onClick={() => setAddActionItemColor(color)}
                                className={`h-6 w-6 rounded-full border ${selected ? `ring-2 ring-[#78a530] ring-offset-1 ${dm ? "ring-offset-[#111520]" : "ring-offset-white"} border-transparent` : dm ? "border-white/[0.20]" : "border-gray-300"}`}
                                style={{ backgroundColor: color }}
                                aria-label={`Select note color ${color}`}
                              />
                            )
                          })}
                        </div>
                      </div>

                      {addActionItemError && (
                        <p className="text-xs text-red-400">{addActionItemError}</p>
                      )}
                    </div>

                    <div className="mt-5 flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={closeAddActionItemModal}
                        disabled={addActionItemSubmitting}
                        className={`rounded-md border px-3 py-1.5 text-xs font-semibold disabled:opacity-50 ${dm ? "border-white/[0.15] text-gray-300 hover:bg-white/[0.06]" : "border-gray-300 text-gray-700 hover:bg-gray-50"}`}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={handleCreateActionItemFromModal}
                        disabled={addActionItemSubmitting}
                        className="rounded-md bg-[#78a530] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#6a9129] disabled:opacity-50"
                      >
                        {addActionItemSubmitting ? "Submitting..." : "Submit"}
                      </button>
                    </div>
                  </div>
                </div>
              </>
            )
          }

          {/* Invite Member Modal */}
          {
            showInviteModal && (
              <>
                {/* Modal */}
                <div className="fixed inset-0 flex items-center justify-center z-50 pointer-events-none">
                  <div className={`rounded-2xl shadow-2xl w-full max-w-md p-8 relative pointer-events-auto border-2 ${dm ? "bg-[#1e2638] border-white/[0.12]" : "bg-white border-gray-300"}`}>
                    {/* Close Button */}
                    <button
                      onClick={() => {
                        setInviteEmail("")
                        setInviteJiraAccountId(null)
                        setInviteError("")
                        setIsInviting(false)
                        setShowInviteModal(false)
                      }}
                      className={`absolute top-4 right-4 text-2xl ${dm ? "text-gray-500 hover:text-gray-300" : "text-gray-400 hover:text-gray-600"}`}
                    >
                      ✕
                    </button>

                    {/* Modal Header */}
                    <h2 className={`text-2xl font-semibold mb-2 ${dm ? "text-gray-100" : "text-gray-900"}`}>Invite Team Member</h2>
                    <p className={`mb-6 ${dm ? "text-gray-400" : "text-gray-600"}`}>Send an invitation to join this project</p>

                    {/* Email Input */}
                    <div className="mb-6">
                      <label htmlFor="invite-email-input" className={`block text-sm font-medium mb-2 ${dm ? "text-gray-300" : "text-gray-700"}`}>
                        Email Address
                      </label>
                      <input
                        id="invite-email-input"
                        type="email"
                        value={inviteEmail}
                        onChange={(e) => setInviteEmail(e.target.value)}
                        onKeyPress={(e) => e.key === "Enter" && handleInviteMember()}
                        placeholder="name@example.com"
                        className={`w-full px-4 py-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent ${dm ? "bg-[#161c2d] border-white/[0.12] text-gray-100 placeholder-gray-500" : "border-gray-300 bg-white text-gray-900"}`}
                        style={dm ? { WebkitBoxShadow: '0 0 0 1000px #161c2d inset', WebkitTextFillColor: '#f3f4f6' } : undefined}
                        autoFocus
                      />
                      {inviteError && (
                        <p className={`mt-2 text-sm ${dm ? "text-red-400" : "text-red-600"}`}>{inviteError}</p>
                      )}
                    </div>

                    {/* Action Buttons */}
                    <div className="flex gap-3">
                      <button
                        onClick={() => {
                          setInviteEmail("")
                          setInviteJiraAccountId(null)
                          setInviteError("")
                          setIsInviting(false)
                          setShowInviteModal(false)
                        }}
                        className={`flex-1 px-6 py-3 border rounded-lg font-semibold transition ${dm ? "border-white/[0.12] text-gray-300 hover:bg-white/[0.06]" : "border-gray-300 text-gray-700 hover:bg-gray-50"}`}
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleInviteMember}
                        disabled={!inviteEmail || !inviteEmail.includes("@") || isInviting}
                        className="flex-1 px-6 py-3 bg-[#78a530] text-white rounded-lg font-semibold hover:bg-[#6a9129] transition disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {isInviting ? "Sending..." : "Send Invite"}
                      </button>
                    </div>
                  </div>
                </div>
              </>
            )
          }

          {/* Action Inbox Modal */}
          {
            showReviewModal && (
              <>
                {/* Modal */}
                <div className="fixed inset-0 flex items-center justify-center z-50 pointer-events-none">
                  <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[80vh] relative pointer-events-auto border-2 border-gray-300 flex flex-col">
                    {/* Close Button */}
                    <button
                      onClick={() => setShowReviewModal(false)}
                      className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 text-2xl z-10"
                    >
                      ✕
                    </button>

                    {/* Modal Header */}
                    <div className="p-6 pb-4 border-b border-gray-100">
                      <h2 className="text-xl font-semibold flex items-center gap-2">
                        <svg className="w-6 h-6 text-green-600" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                        </svg>
                        Action Inbox
                      </h2>
                      <p className="text-sm text-gray-600 mt-1">Items that need your attention</p>
                    </div>

                    {/* Modal Content - Scrollable */}
                    <div className="flex-1 overflow-y-auto p-6 space-y-4">
                      <div className="flex items-center justify-between">
                        <p className="text-xs uppercase tracking-wide font-semibold text-gray-500">People</p>
                        <span className="text-xs text-gray-400">{inboxCounts.people}</span>
                      </div>

                      {pendingInvites.map((invite) => (
                        <div key={invite.email} className="border border-amber-200 bg-white rounded-xl p-4 hover:border-[#78a530] transition">
                          <div className="flex items-start gap-3">
                            <div className="w-8 h-8 rounded-full bg-amber-50 text-amber-700 flex items-center justify-center text-sm font-bold">
                              {invite.name.charAt(0).toUpperCase()}
                            </div>
                            <div className="flex-1">
                              <p className="font-medium">{invite.name}</p>
                              <p className="text-sm text-gray-600">{invite.email}</p>
                              <p className="text-xs text-amber-700 mt-1">Pending invite</p>
                            </div>
                            <button
                              onClick={() => handleRemindMember(invite.email)}
                              className="text-xs px-3 py-1.5 rounded-lg border border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 transition"
                            >
                              Remind
                            </button>
                          </div>
                        </div>
                      ))}

                      {orphanAssignees.map((orphan) => (
                        <div key={orphan.account_id} className="border border-gray-300 bg-gray-50 rounded-xl p-4 hover:border-[#78a530] transition">
                          <div className="flex items-start gap-3 mb-3">
                            <div className="flex-1">
                              <p className="font-medium">{orphan.name}</p>
                              <p className="text-sm text-gray-600">Has {orphan.task_count} {orphan.task_count === 1 ? 'task' : 'tasks'} in Jira, but not a ProMarshal member</p>
                            </div>
                          </div>
                          <div className="flex justify-end gap-2">
                            <button
                              onClick={() => {
                                setInviteEmail("")
                                setInviteJiraAccountId(orphan.account_id)
                                setInviteError("")
                                setShowReviewModal(false)
                                setShowInviteModal(true)
                              }}
                              className="bg-[#78a530] text-white rounded-lg px-4 py-2 text-sm hover:bg-[#6b9429] transition"
                            >
                              Invite to Project
                            </button>
                            <button className="border border-gray-300 rounded-lg px-4 py-2 text-sm hover:bg-gray-50 transition">
                              Remind Later
                            </button>
                          </div>
                        </div>
                      ))}

                      <div className="flex items-center justify-between pt-1">
                        <p className="text-xs uppercase tracking-wide font-semibold text-gray-500">Governance</p>
                        <span className="text-xs text-gray-400">{inboxCounts.governance}</span>
                      </div>

                      {false && charterStatus && (() => {
                        const incompleteStages = []
                        if (charterStatus.stages.goal !== 'finalized') incompleteStages.push('Goals')
                        if (charterStatus.stages.scope !== 'finalized') incompleteStages.push('Scope')
                        if (charterStatus.stages.requirements !== 'finalized') incompleteStages.push('Requirements')
                        if (charterStatus.stages.features_tasks !== 'finalized') incompleteStages.push('Features & Tasks')
                        if (incompleteStages.length === 0) return null

                        return (
                          <div className="border border-yellow-200 bg-white rounded-xl p-4 hover:border-[#78a530] transition">
                            <div className="flex items-start gap-3 mb-3">
                              <svg className="w-5 h-5 text-yellow-600 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" />
                              </svg>
                              <div className="flex-1">
                                <p className="font-medium">Complete Project Charter</p>
                                <p className="text-sm text-gray-600">Missing: {incompleteStages.join(', ')}</p>
                              </div>
                            </div>
                            <div className="flex justify-end">
                              <button
                                onClick={() => {
                                  setShowReviewModal(false)
                                  setActiveNav("Project Charter")
                                }}
                                className="bg-[#78a530] text-white rounded-lg px-4 py-2 text-sm hover:bg-[#6b9429] transition"
                              >
                                Complete
                              </button>
                            </div>
                          </div>
                        )
                      })()}

                      <div className="flex items-center justify-between pt-1">
                        <p className="text-xs uppercase tracking-wide font-semibold text-gray-500">Setup</p>
                        <span className="text-xs text-gray-400">{inboxCounts.setup}</span>
                      </div>

                      {/* Jira Integration Status */}
                      {(() => {
                        const jiraIntegration = getIntegration(selectedProject, 'jira');
                        if (!jiraIntegration || jiraIntegration.status !== 'connected') {
                          return (
                            <div className="border border-yellow-200 bg-white rounded-xl p-4 hover:border-[#78a530] transition">
                              <div className="flex items-start gap-3 mb-3">
                                <svg className="w-5 h-5 text-yellow-600 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" />
                                </svg>
                                <div className="flex-1">
                                  <p className="font-medium">Connect Jira</p>
                                  <p className="text-sm text-gray-600">Connect Jira to sync tasks and track progress automatically.</p>
                                </div>
                              </div>
                              <div className="flex justify-end gap-2">
                                <button
                                  onClick={() => {
                                    setShowReviewModal(false);
                                    openControlPanelTab("Integrate Tools");
                                  }}
                                  className="bg-[#78a530] text-white rounded-lg px-4 py-2 text-sm hover:bg-[#6b9429] transition"
                                >
                                  Connect Jira
                                </button>
                                <button
                                  onClick={() => setShowReviewModal(false)}
                                  className="border border-gray-300 rounded-lg px-4 py-2 text-sm hover:bg-gray-50 transition"
                                >
                                  Later
                                </button>
                              </div>
                            </div>
                          );
                        }
                        return null;
                      })()}

                      {/* Slack Integration Status */}
                      {(() => {
                        const slackIntegration = getIntegration(selectedProject, 'slack');
                        if (!slackIntegration || slackIntegration.status !== 'connected') {
                          return (
                            <div className="border border-yellow-200 bg-white rounded-xl p-4 hover:border-[#78a530] transition">
                              <div className="flex items-start gap-3 mb-3">
                                <svg className="w-5 h-5 text-yellow-600 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" />
                                </svg>
                                <div className="flex-1">
                                  <p className="font-medium">Connect Slack</p>
                                  <p className="text-sm text-gray-600">Connect your Slack workspace to receive real-time notifications and updates from your team.</p>
                                </div>
                              </div>
                              <div className="flex justify-end gap-2">
                                <button
                                  onClick={() => {
                                    setShowReviewModal(false);
                                    openControlPanelTab("Integrate Tools");
                                  }}
                                  className="bg-[#78a530] text-white rounded-lg px-4 py-2 text-sm hover:bg-[#6b9429] transition"
                                >
                                  Connect Slack
                                </button>
                                <button
                                  onClick={() => setShowReviewModal(false)}
                                  className="border border-gray-300 rounded-lg px-4 py-2 text-sm hover:bg-gray-50 transition"
                                >
                                  Later
                                </button>
                              </div>
                            </div>
                          );
                        }
                        return null;
                      })()}
                    </div>

                    {/* Modal Footer */}
                    <div className="p-4 border-t border-gray-100 bg-gray-50 rounded-b-2xl">
                      <button
                        onClick={() => setShowReviewModal(false)}
                        className="w-full py-2 text-sm text-gray-600 hover:text-gray-900 transition"
                      >
                        Close
                      </button>
                    </div>
                  </div>
                </div>
              </>
            )
          }
        </main >
      </div >

      {deleteProjectSuccessBanner && (
        <>
          <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm" />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div
              className={`w-full max-w-2xl rounded-2xl border p-8 shadow-2xl ${
                dm ? "bg-[#111520] border-green-500/40" : "bg-white border-green-300"
              }`}
            >
              <div className="flex items-start gap-4">
                <div className={`mt-0.5 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full ${dm ? "bg-green-500/20 text-green-300" : "bg-green-100 text-green-700"}`}>
                  <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <h3 className={`text-2xl font-semibold ${dm ? "text-green-200" : "text-green-800"}`}>
                    Project Deleted
                  </h3>
                  <p className={`mt-3 text-base leading-relaxed ${dm ? "text-gray-200" : "text-gray-700"}`}>
                    {deleteProjectSuccessBanner}
                  </p>
                  <p className={`mt-4 text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>
                    Redirecting to home...
                  </p>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Slack Channel Selection Modal */}
      {showDeleteProjectModal && selectedProject && (
        <>
          <div
            className="fixed inset-0 bg-black/60 z-50"
            onClick={() => {
              if (isDeletingProject) return
              setShowDeleteProjectModal(false)
            }}
          />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className={`w-full max-w-lg rounded-2xl border shadow-2xl p-6 ${dm ? "bg-[#111520] border-red-500/40" : "bg-white border-red-200"}`}>
              <h3 className={`text-lg font-semibold ${dm ? "text-red-300" : "text-red-700"}`}>Delete Project</h3>
              <p className={`mt-2 text-sm ${dm ? "text-gray-300" : "text-gray-700"}`}>
                This action is permanent. Type <span className="font-semibold">{expectedDeleteConfirmationText}</span> to confirm.
              </p>

              <div className="mt-4">
                <input
                  type="text"
                  value={deleteProjectConfirmText}
                  onChange={(event) => setDeleteProjectConfirmText(event.target.value)}
                  placeholder="Enter project name"
                  className={`w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-500/40 ${dm ? "bg-[#0b1020] border-white/10 text-gray-100 placeholder-gray-500" : "bg-white border-gray-300 text-gray-900"}`}
                />
              </div>

              {deleteProjectError && (
                <p className={`mt-3 text-sm ${dm ? "text-red-300" : "text-red-700"}`}>{deleteProjectError}</p>
              )}

              <div className="mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  disabled={isDeletingProject}
                  onClick={() => {
                    setShowDeleteProjectModal(false)
                    setDeleteProjectError(null)
                    setDeleteProjectConfirmText("")
                  }}
                  className={`px-4 py-2 rounded-lg border transition disabled:opacity-50 ${dm ? "border-white/10 text-gray-300 hover:bg-white/5" : "border-gray-300 text-gray-700 hover:bg-gray-50"}`}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={isDeletingProject || !canConfirmProjectDelete}
                  onClick={handleDeleteProject}
                  className={`px-4 py-2 rounded-lg font-semibold transition disabled:opacity-50 ${dm ? "bg-red-500/25 border border-red-500/40 text-red-200 hover:bg-red-500/35" : "bg-red-600 text-white hover:bg-red-700"}`}
                >
                  {isDeletingProject ? "Deleting..." : "Delete Project"}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      <ConfirmationModal
        isOpen={!!memberToDelete}
        isDarkMode={isDarkMode}
        title="Remove Team Member"
        description={`Are you sure you want to remove ${memberToDelete?.name || memberToDelete?.email || "this member"} from the project? They will lose access immediately.`}
        highlightText={removeMemberError ?? undefined}
        primaryLabel="Remove"
        secondaryLabel="Cancel"
        primaryVariant="danger"
        isPrimaryLoading={isRemovingMember}
        onPrimary={handleConfirmRemoveMember}
        onSecondary={handleCancelRemoveMember}
        onClose={handleCancelRemoveMember}
      />

      {/* Jira Confirmation Modals */}
      <ConfirmationModal
        isOpen={showJiraDisconnectConfirmModal}
        isDarkMode={isDarkMode}
        title="Disconnect Jira"
        description="Do you also want to remove existing project work-item data from ProMarshal for this project?"
        highlightText="If you keep the data, existing work items can still appear in Work Item and Project Health until you clear them."
        primaryLabel="Disconnect + Remove Data"
        secondaryLabel="Disconnect Only"
        primaryVariant="danger"
        isPrimaryLoading={isSubmittingJiraDisconnectChoice}
        onPrimary={() => executeJiraDisconnect(true)}
        onSecondary={() => executeJiraDisconnect(false)}
        onClose={() => {
          if (isSubmittingJiraDisconnectChoice) return
          setShowJiraDisconnectConfirmModal(false)
        }}
      />

      <ConfirmationModal
        isOpen={showJiraStaleDataWarningModal}
        isDarkMode={isDarkMode}
        title="Clear Existing Work Items First"
        description="This project already has work-item data. To avoid mixed sources, clear existing work items before connecting a work-item tool again."
        highlightText={
          jiraStaleWorkItemCount > 0
            ? `${jiraStaleWorkItemCount} existing work item(s) detected for this project.`
            : undefined
        }
        primaryLabel="Clear Existing Data & Continue"
        secondaryLabel="Cancel"
        primaryVariant="danger"
        isPrimaryLoading={isClearingJiraStaleData}
        onPrimary={handleClearStaleJiraDataAndContinue}
        onSecondary={() => {
          if (isClearingJiraStaleData) return
          setShowJiraStaleDataWarningModal(false)
          setIsConnectingJira(false)
        }}
        onClose={() => {
          if (isClearingJiraStaleData) return
          setShowJiraStaleDataWarningModal(false)
          setIsConnectingJira(false)
        }}
      />

      {/* Slack Channel Selection Modal */}
      <SlackChannelModal
        isOpen={showSlackChannelModal}
        isDarkMode={isDarkMode}
        onClose={() => {
          setShowSlackChannelModal(false)
          setSlackSetupProjectId(null)
          sessionStorage.removeItem('slack_modal_shown')
        }}
        projectId={slackSetupProjectId || selectedProject?._id || ""}
        userId={userId}
        backendToken={effectiveBackendToken}
        onSuccess={() => {
          sessionStorage.removeItem('slack_modal_shown')
          // Refresh server data — modal closes itself after user clicks "Got it!"
          router.refresh()
        }}
      />

      {/* Jira Project Selection Modal */}
      <JiraProjectSelectionModal
        isOpen={showJiraProjectSelectionModal}
        isDarkMode={isDarkMode}
        onClose={() => {
          setShowJiraProjectSelectionModal(false)
          setJiraSelectionProjectId(null)
        }}
        projectId={jiraSelectionProjectId || selectedProject?._id || ""}
        userId={userId}
        backendToken={effectiveBackendToken}
        onSuccess={() => {
          if (selectedProject?._id) {
            clearPmBoardBrowserCache(selectedProject._id)
          }
          // Reload page with current project and navigation state preserved
          const params = new URLSearchParams()
          if (selectedProject?._id) {
            params.set('projectId', selectedProject._id)
          }
          params.set('nav', activeNav)
          params.set('tab', activeControlPanelTab)
          window.location.href = `/projects?${params.toString()}`
        }}
      />
    </div >
  )
}
