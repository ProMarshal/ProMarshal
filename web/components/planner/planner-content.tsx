"use client"

import { useState, useEffect, useLayoutEffect, useRef } from "react"
import { useSession } from "next-auth/react"
import { ChatInterface, clearChatMessageCache } from "./chat-interface"
import { RevisionChatOverlay } from "./revision-chat-overlay"
import { TaskGenModal } from "./task-gen-modal"
import { DocumentExtractor } from "./document-extractor"
import { DraftPanel } from "./draft-panel"
import { TopicNav } from "./topic-nav"

interface PlannerContentProps {
    projectId: string
    projectName: string
    projectType?: string
    isDarkMode?: boolean
}

type PlannerMode = "loading" | "welcome" | "in_progress" | "brainstorm" | "stage_complete" | "extract_from_documents" | "split_screen_chat" | "unified_chat"
type CharterMode = "brainstorm" | "document"

interface StageStatus {
    stages: Record<string, string>
    summaries: Record<string, string | null>
    finalized_content: Record<string, string[]>
    current_stage: string
}

interface ChatHistoryMessage {
    role?: string
    content?: string
}

interface StageEntityResponse {
    items: string[]
    out_of_scope?: string[]
    status: string
}

interface PlannerBootstrapResponse {
    project_id: string
    partial?: boolean
    charter_mode?: CharterMode | null
    topics?: {
        groups?: TopicGroup[]
        active_topics?: ActiveTopic[]
        available_optional_topics?: OptionalTopic[]
    }
    status?: StageStatus
    current_stage?: string | null
    entities_by_stage?: Record<string, StageEntityResponse>
    documents?: { file_id: string; filename: string; file_type: string }[]
}

interface PlannerBootstrapStatusResponse {
    status?: string
    has_data?: boolean
    job_id?: string | null
    error?: string | null
    retry_after_ms?: number
    bootstrap?: PlannerBootstrapResponse | null
}

interface PlannerBootstrapProcessingEnvelope {
    __processing__: true
    jobId?: string
    retryAfterMs?: number
    bootstrap?: PlannerBootstrapResponse
}

type Stage = string

interface TopicGroup {
    id: string
    name: string
    display_order?: number
}

interface ActiveTopic {
    topic_id: string
    name: string
    group_id: string
    status: string
    display_order?: number
}

interface OptionalTopic {
    id: string
    name: string
    group_id: string
    description?: string
    display_order?: number
}

const RETRY_DELAYS_MS = [500, 1000, 2000, 4000, 8000]
const BOOTSTRAP_CACHE_TTL_MS = 120000
const BOOTSTRAP_STATUS_POLL_TIMEOUT_MS = 45000
const CHARTER_LOADING_MESSAGES = [
    "AI agents are preparing your charter context...",
    "Mapping goals, scope, requirements, and features...",
    "Loading your charter workspace...",
]
const bootstrapPayloadCache = new Map<string, { payload: PlannerBootstrapResponse; fetchedAt: number }>()
const bootstrapInFlight = new Map<string, Promise<PlannerBootstrapResponse | PlannerBootstrapProcessingEnvelope>>()
const stageEntityGlobalCache = new Map<string, { data: StageEntityResponse; fetchedAt: number }>()
const stageEntityGlobalInFlight = new Map<string, Promise<StageEntityResponse | null>>()

export function PlannerContent({ projectId, projectName, projectType = "Software / Product Development", isDarkMode = false }: PlannerContentProps) {
    const dm = isDarkMode
    const { data: session, status: sessionStatus } = useSession()
    const [stages, setStages] = useState<Record<string, string>>({})
    const [summaries, setSummaries] = useState<Record<string, string | null>>({})
    const [finalizedContent, setFinalizedContent] = useState<Record<string, string[]>>({})
    const [mode, setMode] = useState<PlannerMode>("loading")
    const [activeStage, setActiveStage] = useState<Stage>("goal")
    const [revisionMode, setRevisionMode] = useState(false)
    const [showAddModal, setShowAddModal] = useState(false)
    const [newPointText, setNewPointText] = useState("")
    const [isAddingPoint, setIsAddingPoint] = useState(false)
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
    const [deletePointIndex, setDeletePointIndex] = useState<number | null>(null)
    const [isDeletingPoint, setIsDeletingPoint] = useState(false)
    const [featureTasks, setFeatureTasks] = useState<Record<string, string[]>>({})
    const [selectedFeature, setSelectedFeature] = useState<string | null>(null)
    const [showTaskGenModal, setShowTaskGenModal] = useState(false)
    const [showAddTaskModal, setShowAddTaskModal] = useState(false)
    const [isGeneratingTasks, setIsGeneratingTasks] = useState(false)
    const [newTaskText, setNewTaskText] = useState("")
    const [draftContent, setDraftContent] = useState<Record<string, string[]>>({})
    const [draftOutOfScope, setDraftOutOfScope] = useState<Record<string, string[]>>({})
    const [currentGaps, setCurrentGaps] = useState<string[]>([])
    const [showResetConfirm, setShowResetConfirm] = useState(false)
    const [isResetting, setIsResetting] = useState(false)
    const [charterMode, setCharterMode] = useState<CharterMode | null>(null)
    const [stageOrder, setStageOrder] = useState<string[]>([])
    const [stageLabelMap, setStageLabelMap] = useState<Record<string, string>>({})
    const [topicGroups, setTopicGroups] = useState<TopicGroup[]>([])
    const [activeTopics, setActiveTopics] = useState<ActiveTopic[]>([])
    const [optionalTopics, setOptionalTopics] = useState<OptionalTopic[]>([])
    const [selectedGroupId, setSelectedGroupId] = useState<string>("")
    const [showAddTopicModal, setShowAddTopicModal] = useState(false)
    const [isActivatingTopic, setIsActivatingTopic] = useState(false)
    const [documentViewMode, setDocumentViewMode] = useState<"upload" | "extracted" | "brainstorm">("upload")
    const [uploadedDocs, setUploadedDocs] = useState<{ file_id: string; filename: string; file_type: string }[]>([])
    const [showDocsPopover, setShowDocsPopover] = useState(false)
    const docsPopoverRef = useRef<HTMLDivElement | null>(null)

    const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
    const backendToken = String(session?.user?.backendToken || "").trim()
    const backendFetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
        const headers = new Headers(init.headers || {})
        if (backendToken && !headers.has("Authorization")) {
            headers.set("Authorization", `Bearer ${backendToken}`)
        }
        return window.fetch(input, { ...init, headers })
    }
    const historyCacheRef = useRef(new Map<string, { messages: ChatHistoryMessage[]; fetchedAt: number }>())
    const historyInFlightRef = useRef(new Map<string, Promise<ChatHistoryMessage[]>>())
    const historyCacheTtlMs = 2000
    const stageEntityCacheRef = useRef(new Map<string, { data: StageEntityResponse; fetchedAt: number }>())
    const stageEntityInFlightRef = useRef(new Map<string, Promise<StageEntityResponse | null>>())
    const stageEntityCacheTtlMs = 120000
    const charterModeStorageKey = `pm_charter_mode:${projectId}`
    const charterUiStateKey = `pm_charter_ui_state:${projectId}`
    const bootstrapCacheKey = `pm_charter_bootstrap:${projectId}`
    const [topicsLoaded, setTopicsLoaded] = useState(false)
    const [statusLoaded, setStatusLoaded] = useState(false)
    const [modeLoaded, setModeLoaded] = useState(false)
    const [showKickoff, setShowKickoff] = useState(false)
    const [charterLoadingMessageIndex, setCharterLoadingMessageIndex] = useState(0)
    const kickoffShownRef = useRef<Record<string, boolean>>({})
    const lastSyncedCharterModeRef = useRef<CharterMode | null | undefined>(undefined)
    const activeStageRef = useRef<Stage>("goal")
    const retryIndexRef = useRef(0)
    const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const clearProjectGlobalCaches = (targetProjectId: string) => {
        const normalized = String(targetProjectId || "").trim()
        if (!normalized) return
        bootstrapPayloadCache.delete(normalized)
        bootstrapInFlight.delete(normalized)
        for (const key of Array.from(stageEntityGlobalCache.keys())) {
            if (key.startsWith(`${normalized}:`)) {
                stageEntityGlobalCache.delete(key)
            }
        }
        for (const key of Array.from(stageEntityGlobalInFlight.keys())) {
            if (key.startsWith(`${normalized}:`)) {
                stageEntityGlobalInFlight.delete(key)
            }
        }
    }
    const getResolvedCharterMode = (): CharterMode | null => {
        if (charterMode) return charterMode
        if (typeof window === "undefined") return null
        const stored = window.localStorage.getItem(charterModeStorageKey)
        if (stored === "brainstorm" || stored === "document") {
            return stored
        }
        return null
    }

    useEffect(() => {
        activeStageRef.current = activeStage
    }, [activeStage])

    useEffect(() => {
        const showLoading = mode === "loading" || !modeLoaded
        if (!showLoading) return
        const timer = window.setInterval(() => {
            setCharterLoadingMessageIndex((prev) => (prev + 1) % CHARTER_LOADING_MESSAGES.length)
        }, 2400)
        return () => window.clearInterval(timer)
    }, [mode, modeLoaded])

    const renderCharterLoading = () => (
        <div className="flex items-center justify-center" style={{ height: "calc(100vh - 180px)" }}>
            <div className="flex flex-col items-center gap-1 -translate-y-20">
                <img
                    src="/logos/loading.gif"
                    alt="Loading charter"
                    className="w-72 h-72 object-contain -mb-8"
                    style={dm ? { filter: "brightness(0) invert(1)" } : undefined}
                />
                <div className="h-10 overflow-hidden text-center">
                    <div
                        className="transition-transform duration-500 ease-out"
                        style={{ transform: `translateY(-${charterLoadingMessageIndex * 40}px)` }}
                    >
                        {CHARTER_LOADING_MESSAGES.map((message) => (
                            <p key={message} className={`h-10 text-2xl font-semibold leading-10 ${dm ? "text-white" : "text-gray-800"}`}>
                                {message}
                            </p>
                        ))}
                    </div>
                </div>
                <p className={`text-base tracking-wide ${dm ? "text-white/60" : "text-gray-500"}`}>
                    Building your Project Charter workspace...
                </p>
            </div>
        </div>
    )

    useEffect(() => {
        if (typeof window === "undefined") return
        if (charterMode) {
            window.localStorage.setItem(charterModeStorageKey, charterMode)
        } else {
            window.localStorage.removeItem(charterModeStorageKey)
        }
    }, [charterMode, charterModeStorageKey])

    useEffect(() => {
        if (!modeLoaded) return
        const nextMode = charterMode ?? null
        if (lastSyncedCharterModeRef.current === nextMode) return
        backendFetch(`${backendUrl}/api/planner/${projectId}/charter-mode`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: nextMode })
        }).then(() => {
            lastSyncedCharterModeRef.current = nextMode
        }).catch(() => {
            // ignore sync failure; local mode remains source for UX continuity
        })
    }, [charterMode, modeLoaded, backendUrl, projectId])

    useEffect(() => {
        if (typeof window === "undefined") return
        if (!charterMode) {
            window.sessionStorage.removeItem(charterUiStateKey)
            return
        }
        if (mode === "loading") return
        const payload = JSON.stringify({ stage: activeStage, mode })
        window.sessionStorage.setItem(charterUiStateKey, payload)
    }, [activeStage, mode, charterUiStateKey, charterMode])

    const refreshTopics = async (): Promise<{ ok: boolean; order: string[] }> => {
        try {
            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/topics`)
            if (!response.ok) return { ok: false, order: [] }
            const data = await response.json()

            const groups = Array.isArray(data.groups) ? data.groups : []
            const rawActiveTopics = Array.isArray(data.active_topics) ? data.active_topics : []
            const rawOptionalTopics = Array.isArray(data.available_optional_topics) ? data.available_optional_topics : []

            const normalizedActive = rawActiveTopics
                .map((topic: ActiveTopic) => ({
                    ...topic,
                    topic_id: topic.topic_id,
                    name: topic.name || topic.topic_id,
                    display_order: typeof topic.display_order === "number" ? topic.display_order : 99
                }))
                .filter((topic: ActiveTopic) => Boolean(topic.topic_id))

            const normalizedOptional = rawOptionalTopics
                .map((topic: OptionalTopic) => ({
                    ...topic,
                    id: topic.id,
                    name: topic.name || topic.id,
                    display_order: typeof topic.display_order === "number" ? topic.display_order : 99
                }))
                .filter((topic: OptionalTopic) => Boolean(topic.id))

            if (normalizedActive.length === 0) return { ok: false, order: [] }

            const unique = new Map<string, { stageId: string; name: string; display_order: number }>()
            for (const topic of normalizedActive) {
                const stageId = mapTopicToStageId(topic.topic_id)
                if (!unique.has(stageId)) {
                    unique.set(stageId, {
                        stageId,
                        name: topic.name || topic.topic_id,
                        display_order: topic.display_order ?? 99
                    })
                }
            }

            const sorted = Array.from(unique.values()).sort((a, b) => a.display_order - b.display_order)
            const nextOrder = sorted.map(topic => topic.stageId)

            if (nextOrder.length > 0) {
                setStageOrder(nextOrder)
                setStageLabelMap(prev => {
                    const next = { ...prev }
                    for (const topic of sorted) {
                        if (topic.stageId === "goal") {
                            next[topic.stageId] = "Goals"
                        } else {
                            next[topic.stageId] = topic.name
                        }
                    }
                    return next
                })
                setActiveStage(current => (nextOrder.includes(current) ? current : nextOrder[0]))
            }

            setTopicGroups(groups)
            setActiveTopics(normalizedActive)
            setOptionalTopics(normalizedOptional)

            if (!selectedGroupId && groups.length > 0) {
                const activeGroup = normalizedActive.find((topic: ActiveTopic) => mapTopicToStageId(topic.topic_id) === activeStage)
                setSelectedGroupId(activeGroup?.group_id || groups[0].id)
            }
            setTopicsLoaded(true)
            return { ok: true, order: nextOrder }
        } catch (error) {
            console.error("Error fetching topics:", error)
            return { ok: false, order: [] }
        }
    }

    const getDefaultEntryMode = (): PlannerMode => {
        if (charterMode === "document") {
            return "extract_from_documents"
        }
        if (charterMode === "brainstorm") {
            return "unified_chat"
        }
        return "welcome"
    }

    const getStageLabel = (stage: string): string => {
        return stageLabelMap[stage] || stage
    }

    const mapTopicToStageId = (topicId: string): string => topicId

    const getStatusForStage = (stage: string): string => {
        const fromTopics = activeTopics.find(topic => mapTopicToStageId(topic.topic_id) === stage)
        if (fromTopics?.status) {
            if (fromTopics.status === "active") return "in_progress"
            return fromTopics.status
        }
        return stages[stage] || "pending"
    }

    // Check if any stage has finalized content
    const hasAnyFinalized = () => {
        return stageOrder.some(stage =>
            finalizedContent[stage] && finalizedContent[stage].length > 0
        )
    }

    // Check if all mandatory stages are finalized (goal, scope, requirement, feature)
    const allMandatoryFinalized = () => {
        return stageOrder.every(stage =>
            finalizedContent[stage] && finalizedContent[stage].length > 0
        )
    }

    // Get next unfinalized stage
    const getNextUnfinalizedStage = (): Stage | null => {
        return stageOrder.find(stage =>
            !finalizedContent[stage] || finalizedContent[stage].length === 0
        ) || null
    }

    // Handle back to welcome (without resetting data)
    const handleBackToWelcome = () => {
        setCharterMode(null)
        setMode("welcome")
    }

    // Handle reset charter (Start Fresh)
    const handleResetCharter = async () => {
        setIsResetting(true)
        try {
            const response = await backendFetch(
                `${backendUrl}/api/planner/${projectId}/reset-charter`,
                { method: "DELETE" }
            )
            if (response.ok) {
                clearProjectGlobalCaches(projectId)
                // Reset all local state
                setStages({})
                setSummaries({})
                setFinalizedContent({})
                setDraftContent({})
                setDraftOutOfScope({})
                setFeatureTasks({})
                setActiveStage("goal")
                setCharterMode(null)
                setStageOrder([])
                setStageLabelMap({})
                setTopicGroups([])
                setActiveTopics([])
                setOptionalTopics([])
                setSelectedGroupId("")
                setMode("welcome")
                setTopicsLoaded(false)
                setStatusLoaded(false)
                retryIndexRef.current = 0
                historyCacheRef.current.clear()
                historyInFlightRef.current.clear()
                stageEntityCacheRef.current.clear()
                stageEntityInFlightRef.current.clear()
                clearChatMessageCache()
                kickoffShownRef.current = {}
                setShowResetConfirm(false)
                if (typeof window !== "undefined") {
                    window.sessionStorage.removeItem(bootstrapCacheKey)
                    window.sessionStorage.removeItem(charterUiStateKey)
                    window.localStorage.removeItem(charterModeStorageKey)
                }
            } else {
                alert("Failed to reset charter. Please try again.")
            }
        } catch (error) {
            console.error("Error resetting charter:", error)
            alert("Error resetting charter. Please try again.")
        }
        setIsResetting(false)
    }

    // Get the next stage that needs work
    const getNextIncompleteStage = (): Stage => {
        for (const stage of stageOrder) {
            if (!finalizedContent[stage] || finalizedContent[stage].length === 0) {
                return stage
            }
        }
        return stageOrder[stageOrder.length - 1] || "goal" // All complete
    }

    const handleActivateTopic = async (topic: OptionalTopic) => {
        setIsActivatingTopic(true)
        try {
            const response = await backendFetch(
                `${backendUrl}/api/planner/${projectId}/topics/${topic.id}/activate`,
                { method: "POST" }
            )

            if (response.ok) {
                await refreshTopics()
                setShowAddTopicModal(false)
                setSelectedGroupId(topic.group_id)
                await handleStageChange(mapTopicToStageId(topic.id))
            } else {
                alert("Failed to add topic. Please try again.")
            }
        } catch (error) {
            console.error("Error activating topic:", error)
            alert("Error adding topic. Please try again.")
        }
        setIsActivatingTopic(false)
    }

    // Check if all stages are complete
    const allStagesComplete = (): boolean => {
        return stageOrder.every(stage =>
            finalizedContent[stage] && finalizedContent[stage].length > 0
        )
    }

    // Get all finalized stages for cards display
    const getFinalizedStages = (): Stage[] => {
        return stageOrder.filter(stage =>
            finalizedContent[stage] && finalizedContent[stage].length > 0
        )
    }

    // Helper to check if conversation has messages (includes extracted content)
    const getHistoryMessages = async (stage: string, options?: { force?: boolean }): Promise<ChatHistoryMessage[]> => {
        const cacheKey = `${projectId}:${stage}`
        const now = Date.now()
        const cached = historyCacheRef.current.get(cacheKey)

        if (cached && !options?.force && now - cached.fetchedAt < historyCacheTtlMs) {
            return cached.messages
        }

        const inFlight = historyInFlightRef.current.get(cacheKey)
        if (inFlight) {
            return inFlight
        }

        const request = backendFetch(`${backendUrl}/api/planner/${projectId}/history/${stage}`)
            .then(async (historyRes) => {
                if (!historyRes.ok) return []
                const historyData = await historyRes.json()
                const messages = Array.isArray(historyData.messages)
                    ? (historyData.messages as ChatHistoryMessage[])
                    : []
                historyCacheRef.current.set(cacheKey, { messages, fetchedAt: Date.now() })
                return messages
            })
            .catch(() => [])
            .finally(() => {
                historyInFlightRef.current.delete(cacheKey)
            })

        historyInFlightRef.current.set(cacheKey, request)
        return request
    }

    const getStageEntityData = async (stage: string, options?: { force?: boolean }): Promise<StageEntityResponse | null> => {
        const cacheKey = `${projectId}:${stage}`
        const now = Date.now()
        const cached = stageEntityCacheRef.current.get(cacheKey)
        const globalCached = stageEntityGlobalCache.get(cacheKey)

        if (cached && !options?.force && now - cached.fetchedAt < stageEntityCacheTtlMs) {
            return cached.data
        }
        if (globalCached && !options?.force && now - globalCached.fetchedAt < stageEntityCacheTtlMs) {
            stageEntityCacheRef.current.set(cacheKey, globalCached)
            return globalCached.data
        }

        const inFlight = stageEntityInFlightRef.current.get(cacheKey)
        if (inFlight) {
            return inFlight
        }
        const globalInFlight = stageEntityGlobalInFlight.get(cacheKey)
        if (globalInFlight) {
            return globalInFlight
        }

        const request = backendFetch(`${backendUrl}/api/planner/${projectId}/entities/${stage}`)
            .then(async (response) => {
                if (!response.ok) return null
                const data = await response.json()
                const outOfScope = Array.isArray(data.out_of_scope)
                    ? data.out_of_scope
                    : Array.isArray(data.out_of_scope_items)
                        ? data.out_of_scope_items
                        : []
                const normalized: StageEntityResponse = {
                    items: Array.isArray(data.items) ? data.items : [],
                    out_of_scope: outOfScope,
                    status: typeof data.status === "string" ? data.status : "pending"
                }
                stageEntityCacheRef.current.set(cacheKey, { data: normalized, fetchedAt: Date.now() })
                stageEntityGlobalCache.set(cacheKey, { data: normalized, fetchedAt: Date.now() })
                return normalized
            })
            .catch(() => null)
            .finally(() => {
                stageEntityInFlightRef.current.delete(cacheKey)
                stageEntityGlobalInFlight.delete(cacheKey)
            })

        stageEntityInFlightRef.current.set(cacheKey, request)
        stageEntityGlobalInFlight.set(cacheKey, request)
        return request
    }

    const refreshStageEntities = async (stage: Stage, options?: { force?: boolean }) => {
        const data = await getStageEntityData(stage, options)
        if (!data) return
        setDraftContent(prev => ({ ...prev, [stage]: data.items }))
        if (stage === "scope") {
            setDraftOutOfScope(prev => ({ ...prev, [stage]: data.out_of_scope || [] }))
        }
    }

    const persistDraft = async (stage: Stage, items: string[], outOfScope?: string[]) => {
        try {
            await backendFetch(`${backendUrl}/api/planner/${projectId}/draft/${stage}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ items, out_of_scope: outOfScope || [] })
            })
        } catch {
            // ignore
        }
        setStages(prev => ({ ...prev, [stage]: "in_progress" }))
        setFinalizedContent(prev => ({ ...prev, [stage]: [] }))
    }

    // Helper to check if conversation has messages (includes extracted content)
    const checkHasMessages = async (stage: string): Promise<boolean> => {
        const messages = await getHistoryMessages(stage)
        return messages.length > 0
    }

    // Helper to check if conversation has actual user messages (for backward compatibility)
    const checkHasUserMessages = async (stage: string): Promise<boolean> => {
        const messages = await getHistoryMessages(stage)
        return messages.some((m: { role?: string }) => m?.role === "user")
    }

    const getPreferredStage = (statusData: StageStatus, order: string[]): Stage => {
        const fallbackOrder = order.length > 0 ? order : ["goal"]
        const nextIncomplete = fallbackOrder.find(stage => !(statusData.finalized_content?.[stage]?.length))
        return (nextIncomplete || statusData.current_stage || fallbackOrder[0] || "goal") as Stage
    }

    const resolveModeForStage = (
        stage: Stage,
        resolvedCharterMode: CharterMode | null,
        stageStatuses: Record<string, string>,
        finalContent: Record<string, string[]>
    ): PlannerMode => {
        if (!resolvedCharterMode) return "welcome"
        if (resolvedCharterMode === "document") return "extract_from_documents"
        if ((finalContent[stage]?.length || 0) > 0) return "stage_complete"
        if (stageStatuses[stage] === "in_progress") return "unified_chat"
        return "unified_chat"
    }

    const applyBootstrapPayload = (
        payload: PlannerBootstrapResponse,
        options?: { honorUiState?: boolean; isPartial?: boolean }
    ) => {
        const isPartialPayload = options?.isPartial === true || payload.partial === true
        const topicData = payload.topics || {}
        const groups = Array.isArray(topicData.groups) ? topicData.groups : []
        const rawActiveTopics = Array.isArray(topicData.active_topics) ? topicData.active_topics : []
        const rawOptionalTopics = Array.isArray(topicData.available_optional_topics) ? topicData.available_optional_topics : []

        const normalizedActive = rawActiveTopics
            .map((topic: ActiveTopic) => ({
                ...topic,
                topic_id: topic.topic_id,
                name: topic.name || topic.topic_id,
                display_order: typeof topic.display_order === "number" ? topic.display_order : 99
            }))
            .filter((topic: ActiveTopic) => Boolean(topic.topic_id))

        const normalizedOptional = rawOptionalTopics
            .map((topic: OptionalTopic) => ({
                ...topic,
                id: topic.id,
                name: topic.name || topic.id,
                display_order: typeof topic.display_order === "number" ? topic.display_order : 99
            }))
            .filter((topic: OptionalTopic) => Boolean(topic.id))

        const unique = new Map<string, { stageId: string; name: string; display_order: number }>()
        for (const topic of normalizedActive) {
            const stageId = mapTopicToStageId(topic.topic_id)
            if (!unique.has(stageId)) {
                unique.set(stageId, {
                    stageId,
                    name: topic.name || topic.topic_id,
                    display_order: topic.display_order ?? 99
                })
            }
        }

        const sorted = Array.from(unique.values()).sort((a, b) => a.display_order - b.display_order)
        const nextOrder = sorted.map(topic => topic.stageId)
        const statusData: StageStatus = payload.status || {
            current_stage: payload.current_stage || "goal",
            stages: {},
            finalized_content: {},
            summaries: {},
        }
        const resolvedCharterMode = (payload.charter_mode === "brainstorm" || payload.charter_mode === "document")
            ? payload.charter_mode
            : getResolvedCharterMode()
        lastSyncedCharterModeRef.current = payload.charter_mode === "brainstorm" || payload.charter_mode === "document"
            ? payload.charter_mode
            : null

        let preferredStage: Stage = getPreferredStage(statusData, nextOrder)
        if (typeof window !== "undefined" && options?.honorUiState) {
            const rawUi = window.sessionStorage.getItem(charterUiStateKey)
            if (rawUi) {
                try {
                    const parsed = JSON.parse(rawUi)
                    if (parsed?.stage && (nextOrder.includes(parsed.stage) || parsed.stage === preferredStage)) {
                        preferredStage = parsed.stage
                    }
                } catch {
                    // ignore malformed ui state
                }
            }
        }

        setStageOrder(nextOrder)
        setStageLabelMap(prev => {
            const next = { ...prev }
            for (const topic of sorted) {
                next[topic.stageId] = topic.stageId === "goal" ? "Goals" : topic.name
            }
            return next
        })
        setTopicGroups(groups)
        setActiveTopics(normalizedActive)
        setOptionalTopics(normalizedOptional)
        setSelectedGroupId(current => {
            if (current) return current
            const activeGroup = normalizedActive.find((topic: ActiveTopic) => mapTopicToStageId(topic.topic_id) === preferredStage)
            return activeGroup?.group_id || groups[0]?.id || ""
        })
        if (isPartialPayload) {
            const incomingStages = statusData.stages || {}
            setStages(prev => {
                const merged = { ...prev }
                for (const [stage, incomingStatus] of Object.entries(incomingStages)) {
                    const prevStatus = String(merged[stage] || "")
                    if (prevStatus === "finalized" && incomingStatus !== "finalized") {
                        continue
                    }
                    merged[stage] = incomingStatus
                }
                return merged
            })
            setSummaries(prev => ({ ...prev, ...(statusData.summaries || {}) }))
            setFinalizedContent(prev => {
                const merged = { ...prev }
                for (const [stage, items] of Object.entries(statusData.finalized_content || {})) {
                    if (Array.isArray(items) && items.length > 0) {
                        merged[stage] = items
                    }
                }
                return merged
            })
        } else {
            setStages(statusData.stages || {})
            setSummaries(statusData.summaries || {})
            setFinalizedContent(statusData.finalized_content || {})
        }
        setUploadedDocs(Array.isArray(payload.documents) ? payload.documents : [])
        const bootstrapEntities = payload.entities_by_stage || {}
        if (Object.keys(bootstrapEntities).length > 0) {
            setDraftContent(prev => {
                const next = { ...prev }
                for (const [stage, data] of Object.entries(bootstrapEntities)) {
                    next[stage] = Array.isArray(data?.items) ? data.items : []
                    const key = `${projectId}:${stage}`
                    const normalized: StageEntityResponse = {
                        items: Array.isArray(data?.items) ? data.items : [],
                        out_of_scope: Array.isArray(data?.out_of_scope) ? data.out_of_scope : [],
                        status: typeof data?.status === "string" ? data.status : "pending",
                    }
                    const cacheEntry = { data: normalized, fetchedAt: Date.now() }
                    stageEntityCacheRef.current.set(key, cacheEntry)
                    stageEntityGlobalCache.set(key, cacheEntry)
                }
                return next
            })
            if (Object.prototype.hasOwnProperty.call(bootstrapEntities, "scope")) {
                const scopeData = bootstrapEntities.scope
                setDraftOutOfScope(prev => ({
                    ...prev,
                    scope: Array.isArray(scopeData?.out_of_scope) ? scopeData.out_of_scope : [],
                }))
            }
        }

        if (resolvedCharterMode === "brainstorm" || resolvedCharterMode === "document") {
            setCharterMode(resolvedCharterMode)
        } else {
            setCharterMode(null)
        }
        setActiveStage(preferredStage)
        setMode(resolveModeForStage(preferredStage, resolvedCharterMode, statusData.stages || {}, statusData.finalized_content || {}))
        setTopicsLoaded(true)
        setStatusLoaded(true)
    }

    const refreshStatus = async (orderOverride?: string[]): Promise<boolean> => {
        const effectiveOrder = orderOverride && orderOverride.length > 0 ? orderOverride : stageOrder
        try {
            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/status`)
            if (!response.ok) {
                if (response.status === 404) {
                    const modeFromStorage = getResolvedCharterMode()
                    setStages({})
                    setSummaries({})
                    setFinalizedContent({})
                    setActiveStage("goal")
                    setMode(resolveModeForStage("goal", modeFromStorage, {}, {}))
                    setStatusLoaded(true)
                    return true
                }
                return false
            }

            const data: StageStatus = await response.json()
            setStages(data.stages || {})
            setSummaries(data.summaries || {})
            setFinalizedContent(data.finalized_content || {})
            const nextStage = getPreferredStage(data, effectiveOrder)
            setActiveStage(nextStage)
            setMode(resolveModeForStage(nextStage, getResolvedCharterMode(), data.stages || {}, data.finalized_content || {}))
            setStatusLoaded(true)
            return true
        } catch (error) {
            console.error("Error fetching planner status:", error)
            return false
        }
    }

    useEffect(() => {
        let cancelled = false
        let hasWarmCache = false
        const isAuthTokenMissing = sessionStatus === "authenticated" && !backendToken

        const clearRetryTimer = () => {
            if (retryTimerRef.current) {
                clearTimeout(retryTimerRef.current)
                retryTimerRef.current = null
            }
        }

        const scheduleRetry = () => {
            const delay = RETRY_DELAYS_MS[Math.min(retryIndexRef.current, RETRY_DELAYS_MS.length - 1)]
            retryIndexRef.current += 1
            clearRetryTimer()
            retryTimerRef.current = setTimeout(() => {
                void loadBootstrap(false)
            }, delay)
        }

        if (sessionStatus === "loading") {
            return () => {
                cancelled = true
                clearRetryTimer()
            }
        }

        if (sessionStatus === "unauthenticated") {
            setModeLoaded(true)
            setMode("welcome")
            setTopicsLoaded(false)
            setStatusLoaded(true)
            return () => {
                cancelled = true
                clearRetryTimer()
            }
        }

        if (isAuthTokenMissing) {
            return () => {
                cancelled = true
                clearRetryTimer()
            }
        }

        const loadBootstrap = async (honorUiState: boolean) => {
            try {
                const globalCacheKey = `${projectId}`
                const now = Date.now()
                const cached = bootstrapPayloadCache.get(globalCacheKey)
                const cachedHasEntities = cached?.payload?.entities_by_stage && Object.keys(cached.payload.entities_by_stage).length > 0
                const cachedIsReady = cached?.payload?.partial !== true
                if (cached && cachedHasEntities && cachedIsReady && now - cached.fetchedAt < BOOTSTRAP_CACHE_TTL_MS) {
                    if (cancelled) return
                    applyBootstrapPayload(cached.payload, { honorUiState })
                    setModeLoaded(true)
                    hasWarmCache = true
                    return
                }

                const inFlight = bootstrapInFlight.get(globalCacheKey)
                const request = inFlight ?? backendFetch(`${backendUrl}/api/planner/${projectId}/bootstrap`).then(async (response) => {
                    if (response.status === 202) {
                        const payload = await response.json().catch(() => ({}))
                        const bootstrap = payload?.bootstrap as PlannerBootstrapResponse | undefined
                        return {
                            __processing__: true,
                            jobId: typeof payload?.job_id === "string" ? payload.job_id : undefined,
                            retryAfterMs: Number.isFinite(payload?.retry_after_ms) ? Number(payload.retry_after_ms) : 1000,
                            bootstrap,
                        } as PlannerBootstrapProcessingEnvelope
                    }
                    if (!response.ok) {
                        const err = new Error(`bootstrap_http_${response.status}`)
                        ;(err as Error & { status?: number; authPending?: boolean }).status = response.status
                        ;(err as Error & { status?: number; authPending?: boolean }).authPending =
                            (response.status === 401 || response.status === 403) && isAuthTokenMissing
                        throw err
                    }
                    return await response.json() as PlannerBootstrapResponse
                })
                if (!inFlight) {
                    bootstrapInFlight.set(globalCacheKey, request)
                }
                const data = await request
                if (!inFlight) {
                    bootstrapInFlight.delete(globalCacheKey)
                }
                if (cancelled) return
                if ((data as PlannerBootstrapProcessingEnvelope).__processing__ === true) {
                    const processingData = data as PlannerBootstrapProcessingEnvelope
                    if (processingData.bootstrap) {
                        applyBootstrapPayload(processingData.bootstrap, { honorUiState, isPartial: true })
                        setModeLoaded(true)
                    }

                    const startedAt = Date.now()
                    let delayMs = Math.max(750, Number(processingData.retryAfterMs || 1000))
                    while (!cancelled && Date.now() - startedAt < BOOTSTRAP_STATUS_POLL_TIMEOUT_MS) {
                        await new Promise(resolve => setTimeout(resolve, delayMs))
                        if (cancelled) return
                        try {
                            const statusUrl = `${backendUrl}/api/planner/${projectId}/bootstrap-status?job_id=${encodeURIComponent(processingData.jobId || "")}`
                            const statusRes = await backendFetch(statusUrl)
                            if (!statusRes.ok) {
                                if (statusRes.status === 401 || statusRes.status === 403) {
                                    continue
                                }
                                throw new Error(`bootstrap_status_http_${statusRes.status}`)
                            }
                            const statusPayload = await statusRes.json() as PlannerBootstrapStatusResponse
                            const statusValue = String(statusPayload?.status || "").toLowerCase()
                            if (statusValue === "ready" && statusPayload?.bootstrap) {
                                const fullBootstrap = statusPayload.bootstrap
                                bootstrapPayloadCache.set(globalCacheKey, { payload: fullBootstrap, fetchedAt: Date.now() })
                                applyBootstrapPayload(fullBootstrap, { honorUiState: false })
                                setModeLoaded(true)
                                if (typeof window !== "undefined") {
                                    window.sessionStorage.setItem(
                                        bootstrapCacheKey,
                                        JSON.stringify({ fetchedAt: Date.now(), payload: fullBootstrap })
                                    )
                                }
                                return
                            }
                            if (statusValue === "failed") {
                                break
                            }
                            const retryHint = Number(statusPayload?.retry_after_ms)
                            if (Number.isFinite(retryHint) && retryHint > 0) {
                                delayMs = Math.min(8000, Math.max(750, retryHint))
                            } else {
                                delayMs = Math.min(8000, Math.max(750, Math.floor(delayMs * 1.5)))
                            }
                        } catch {
                            delayMs = Math.min(8000, Math.max(750, Math.floor(delayMs * 1.5)))
                        }
                    }

                    if (!hasWarmCache) {
                        scheduleRetry()
                    }
                    return
                }

                const readyData = data as PlannerBootstrapResponse
                if (readyData.partial !== true) {
                    bootstrapPayloadCache.set(globalCacheKey, { payload: readyData, fetchedAt: Date.now() })
                }
                applyBootstrapPayload(readyData, { honorUiState })
                setModeLoaded(true)
                if (typeof window !== "undefined" && readyData.partial !== true) {
                    window.sessionStorage.setItem(
                        bootstrapCacheKey,
                        JSON.stringify({ fetchedAt: Date.now(), payload: readyData })
                    )
                }
            } catch (error) {
                const status = (error as Error & { status?: number; authPending?: boolean })?.status
                const authPending = (error as Error & { status?: number; authPending?: boolean })?.authPending
                bootstrapInFlight.delete(`${projectId}`)
                if (cancelled) return
                if (status === 401 || status === 403) {
                    // Authorization can briefly lag while backend token/session hydrates.
                    if (authPending || !hasWarmCache) {
                        scheduleRetry()
                    }
                    return
                }
                if (status === 404) {
                    if (!cancelled) {
                        setCharterMode(null)
                        setMode("welcome")
                        setTopicsLoaded(false)
                        setStatusLoaded(true)
                        setModeLoaded(true)
                    }
                    return
                }
                if (cancelled) return
                if (!hasWarmCache) {
                    scheduleRetry()
                } else {
                    console.error("Error refreshing planner bootstrap:", error)
                }
            }
        }

        retryIndexRef.current = 0
        setMode("loading")
        setModeLoaded(false)
        setTopicsLoaded(false)
        setStatusLoaded(false)
        clearRetryTimer()

        if (typeof window !== "undefined") {
            const raw = window.sessionStorage.getItem(bootstrapCacheKey)
            if (raw) {
                try {
                    const parsed = JSON.parse(raw)
                    const fetchedAt = Number(parsed?.fetchedAt || 0)
                    const payload = parsed?.payload as PlannerBootstrapResponse | undefined
                    const hasEntitiesMap = payload?.entities_by_stage && Object.keys(payload.entities_by_stage).length > 0
                    const isReadyPayload = payload?.partial !== true
                    if (Date.now() - fetchedAt < BOOTSTRAP_CACHE_TTL_MS && payload && hasEntitiesMap && isReadyPayload) {
                        applyBootstrapPayload(payload, { honorUiState: true })
                        setModeLoaded(true)
                        hasWarmCache = true
                    }
                } catch {
                    // ignore malformed bootstrap cache
                }
            }
        }

        void loadBootstrap(true)

        return () => {
            cancelled = true
            clearRetryTimer()
        }
    }, [projectId, backendUrl, bootstrapCacheKey, charterUiStateKey, backendToken, sessionStatus])

    useEffect(() => {
        historyCacheRef.current.clear()
        historyInFlightRef.current.clear()
        stageEntityCacheRef.current.clear()
        stageEntityInFlightRef.current.clear()
    }, [projectId])

    useLayoutEffect(() => {
        if (mode !== "unified_chat" || charterMode !== "brainstorm" || activeStage !== "goal") {
            setShowKickoff(false)
            return
        }

        const key = `${projectId}:goal`
        const hasDraft = (draftContent[activeStage]?.length || 0) > 0
        const hasFinal = (finalizedContent[activeStage]?.length || 0) > 0
        const hasOutOfScope = (activeStage as string) === "scope" && (draftOutOfScope[activeStage]?.length || 0) > 0
        const shouldStart = !hasDraft && !hasFinal && !hasOutOfScope && !kickoffShownRef.current[key]
        if (!shouldStart) {
            if (showKickoff) {
                return
            }
            setShowKickoff(false)
            return
        }

        kickoffShownRef.current[key] = true
        let cancelled = false

        setShowKickoff(true)

        const timer = setTimeout(() => {
            if (!cancelled) {
                setShowKickoff(false)
            }
        }, 5000)

        const cancelIfUserMessages = async () => {
            const hasUserMsgs = await checkHasUserMessages(activeStage)
            if (!cancelled && hasUserMsgs) {
                setShowKickoff(false)
                clearTimeout(timer)
            }
        }
        cancelIfUserMessages()

        return () => {
            cancelled = true
            clearTimeout(timer)
        }
    }, [mode, charterMode, activeStage, projectId, draftContent, finalizedContent, draftOutOfScope])

    useEffect(() => {
        if (mode === "welcome" && charterMode) {
            setMode(getDefaultEntryMode())
        }
    }, [mode, charterMode])

    // Fetch draft content when unified/split chat or document extracted view opens
    useEffect(() => {
        const fetchDraftContent = async () => {
            if (mode === "split_screen_chat" || mode === "unified_chat" || mode === "extract_from_documents") {
                if (Object.prototype.hasOwnProperty.call(draftContent, activeStage)) {
                    return
                }
                await refreshStageEntities(activeStage)
            }
        }
        fetchDraftContent()
    }, [mode, activeStage, draftContent])

    useEffect(() => {
        if (mode === "stage_complete") {
            refreshStageEntities(activeStage)
        }
    }, [mode, activeStage])

    const refreshUploadedDocuments = async () => {
        try {
            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/documents`)
            if (!response.ok) return
            const data = await response.json()
            setUploadedDocs(Array.isArray(data.documents) ? data.documents : [])
        } catch {
            // ignore
        }
    }

    useEffect(() => {
        if (!showDocsPopover) return
        const handleClick = (event: MouseEvent) => {
            if (!docsPopoverRef.current) return
            if (!docsPopoverRef.current.contains(event.target as Node)) {
                setShowDocsPopover(false)
            }
        }
        document.addEventListener("mousedown", handleClick)
        return () => document.removeEventListener("mousedown", handleClick)
    }, [showDocsPopover])

    const handleFinalize = (content: string[]) => {
        console.log("✅ Finalized:", activeStage, content)
        setFinalizedContent(prev => ({ ...prev, [activeStage]: content }))
        setStages(prev => ({ ...prev, [activeStage]: "finalized" }))
        setMode("stage_complete") // Immediately show finalized view
        if (activeStage === "scope") {
            refreshStageEntities(activeStage, { force: true })
        }
        // No modal - just stay on stage_complete
    }

    const finalizeDraftStage = async () => {
        try {
            const content = draftContent[activeStage] || []
            const outOfScope = activeStage === "scope"
                ? (draftOutOfScope[activeStage] || [])
                : undefined
            if (content.length === 0) {
                return
            }

            const finalizeRes = await backendFetch(`${backendUrl}/api/planner/${projectId}/finalize/${activeStage}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content, out_of_scope: outOfScope })
            })
            if (finalizeRes.ok) {
                handleFinalize(content)

                const nextStage = getNextUnfinalizedStage()
                if (nextStage && nextStage !== activeStage) {
                    setActiveStage(nextStage)
                } else if (allMandatoryFinalized()) {
                    setMode("stage_complete")
                }
            } else {
                console.error("Failed to finalize content")
            }
        } catch (error) {
            console.error("Error finalizing:", error)
        }
    }

    const handleStageChange = (stage: string) => {
        const nextStage = stage as Stage
        setActiveStage(nextStage)
        setCurrentGaps([])

        // Keep document mode stable while navigating stages.
        if (mode === "extract_from_documents") {
            return
        }

        const immediateMode = resolveModeForStage(nextStage, charterMode, stages, finalizedContent)
        setMode(immediateMode)

        // Non-blocking history refinement so submenu switch remains instant.
        if (charterMode === "brainstorm" && immediateMode !== "stage_complete") {
            void checkHasMessages(nextStage).then((hasAnyMsgs) => {
                if (activeStageRef.current !== nextStage) return
                if (hasAnyMsgs) {
                    setMode("unified_chat")
                }
            })
        }
    }

    const handleAddPoint = async () => {
        if (!newPointText.trim()) return

        setIsAddingPoint(true)
        try {
            const baseItems = (finalizedContent[activeStage]?.length ?? 0) > 0
                ? finalizedContent[activeStage]
                : (draftContent[activeStage] || [])
            const updatedItems = [...baseItems, newPointText.trim()]
            setDraftContent(prev => ({ ...prev, [activeStage]: updatedItems }))
            setShowAddModal(false)
            setNewPointText("")
            await persistDraft(
                activeStage,
                updatedItems,
                activeStage === "scope" ? (draftOutOfScope[activeStage] || []) : undefined
            )
        } catch (error) {
            console.error("Error adding point:", error)
            alert("Error adding point. Please try again.")
        }
        setIsAddingPoint(false)
    }

    const handleDeletePoint = async () => {
        if (deletePointIndex === null) return

        setIsDeletingPoint(true)
        try {
            const baseItems = (finalizedContent[activeStage]?.length ?? 0) > 0
                ? finalizedContent[activeStage]
                : (draftContent[activeStage] || [])
            const updatedItems = baseItems.filter((_, idx) => idx !== deletePointIndex)
            setDraftContent(prev => ({ ...prev, [activeStage]: updatedItems }))
            await persistDraft(
                activeStage,
                updatedItems,
                activeStage === "scope" ? (draftOutOfScope[activeStage] || []) : undefined
            )
            if (updatedItems.length === 0) {
                setStages(prev => ({
                    ...prev,
                    [activeStage]: "pending"
                }))
            }
            setShowDeleteConfirm(false)
            setDeletePointIndex(null)
        } catch (error) {
            console.error("Error deleting point:", error)
            alert("Error deleting point. Please try again.")
        }
        setIsDeletingPoint(false)
    }

    const handleAutoGenerateTasks = async (feature: string) => {
        setIsGeneratingTasks(true)
        try {
            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/generate-tasks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ feature, stage: "feature" })
            })

            if (response.ok) {
                const data = await response.json()
                setFeatureTasks(prev => ({ ...prev, [feature]: data.tasks }))
            } else {
                alert("Failed to generate tasks. Please try again.")
            }
        } catch (error) {
            console.error("Error generating tasks:", error)
            alert("Error generating tasks. Please try again.")
        }
        setIsGeneratingTasks(false)
    }

    const handleClearTasks = (feature: string) => {
        if (confirm(`Clear all tasks for "${feature}"?`)) {
            setFeatureTasks(prev => {
                const updated = { ...prev }
                delete updated[feature]
                return updated
            })
        }
    }

    const handleAddTask = () => {
        if (!newTaskText.trim() || !selectedFeature) return

        setFeatureTasks(prev => ({
            ...prev,
            [selectedFeature]: [...(prev[selectedFeature] || []), newTaskText.trim()]
        }))

        setNewTaskText("")
        setShowAddTaskModal(false)
        setSelectedFeature(null)
    }

    const handleDeleteTask = (feature: string, taskIndex: number) => {
        setFeatureTasks(prev => {
            const tasks = [...(prev[feature] || [])]
            tasks.splice(taskIndex, 1)

            if (tasks.length === 0) {
                const updated = { ...prev }
                delete updated[feature]
                return updated
            }

            return { ...prev, [feature]: tasks }
        })
    }


    // Loading state
    if (mode === "loading") {
        return renderCharterLoading()
    }

    // Welcome/Entry Screen (no finalized content yet OR starting new stage)
    const renderWelcomeContent = () => (
        <div className="max-w-3xl mx-auto">
            {/* Fancy welcome header - no finalized cards shown */}
            <div className="text-center mb-10">
                {getFinalizedStages().length === 0 ? (
                    <>
                        <div className="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-[#78a530] to-[#5a7d24] rounded-2xl mb-4 shadow-lg">
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 text-white" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                            </svg>
                        </div>
                        <h2 className={`text-2xl font-bold mb-3 ${dm ? "text-gray-100" : "text-gray-900"}`}>
                            Welcome to your Project Charter
                        </h2>
                        <p className={`text-base max-w-md mx-auto ${dm ? "text-gray-400" : "text-gray-600"}`}>
                            Let's bring <span className="font-semibold text-[#78a530]">{projectName}</span> to life!
                            Start by defining your project goals with our AI assistant.
                        </p>
                    </>
                ) : (
                    <>
                        <div className="inline-flex items-center gap-2 px-4 py-2 bg-[#78a530]/10 text-[#78a530] rounded-full text-sm font-medium mb-4">
                            <span>✓</span> {getFinalizedStages().length} of {stageOrder.length} stages complete
                        </div>
                        <h2 className={`text-xl font-bold mb-2 ${dm ? "text-gray-100" : "text-gray-900"}`}>
                            Time to define your {getStageLabel(activeStage).toLowerCase()}
                        </h2>
                        <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-600"}`}>
                            Continue building <span className="font-semibold text-[#78a530]">{projectName}</span>
                        </p>
                    </>
                )}
            </div>

            {/* Action Cards */}
            <div className="grid grid-cols-2 gap-6">
                {/* Brainstorm Card */}
                <button
                    onClick={() => {
                        setCharterMode("brainstorm")
                        setMode("unified_chat")
                    }}
                    className={`group p-6 border-2 border-transparent hover:border-[#78a530] transition-all duration-300 text-left rounded-2xl ${dm ? "bg-[#1e2638]" : "bg-gray-50 border border-gray-500 shadow-sm"}`}
                >
                    <div className="w-12 h-12 bg-gradient-to-br from-[#78a530] to-[#5d8024] rounded-xl flex items-center justify-center text-white mb-4 marshal-sparkle">
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                        </svg>
                    </div>
                    <h3 className={`text-base font-semibold mb-2 group-hover:text-[#78a530] ${dm ? "text-gray-100" : "text-gray-900"}`}>
                        Brainstorm with Marshal
                    </h3>
                    <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-600"}`}>
                        Our AI assistant will guide you through defining your {getStageLabel(activeStage).toLowerCase()} step by step.
                    </p>
                </button>

                {/* Extract from Documents Card */}
                <button
                    onClick={() => {
                        setCharterMode("document")
                        setMode("extract_from_documents")
                    }}
                    className={`group p-6 border-2 border-transparent hover:border-blue-400 transition-all duration-300 text-left rounded-2xl ${dm ? "bg-[#1e2638]" : "bg-gray-50 border border-gray-500 shadow-sm"}`}
                >
                    <div className="w-12 h-12 bg-gradient-to-br from-blue-500 to-blue-600 rounded-xl flex items-center justify-center text-white mb-4 marshal-sparkle">
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                        </svg>
                    </div>
                    <h3 className={`text-base font-semibold mb-2 group-hover:text-blue-500 ${dm ? "text-gray-100" : "text-gray-900"}`}>
                        Extract from Documents
                    </h3>
                    <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-600"}`}>
                        Upload existing docs (Word, PDF, Markdown) and we'll extract the {getStageLabel(activeStage).toLowerCase()} automatically.
                    </p>
                </button>
            </div>
        </div>
    )

    // In-progress conversation (user started but didn't finalize) - Auto-open split screen
    const renderInProgressContent = () => (
        <div className="max-w-2xl mx-auto">
            <div className="text-center py-16">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[#78a530] mx-auto"></div>
                <p className="text-sm text-gray-600 mt-4">Opening brainstorm...</p>
            </div>
        </div>
    )

    // Stage Complete View (showing finalized content for a stage)
    const renderStageCompleteContent = () => (
        <div className="max-w-5xl mx-auto">
            {(() => {
                const contentToShow = (finalizedContent[activeStage]?.length ?? 0) > 0
                    ? finalizedContent[activeStage]
                    : draftContent[activeStage] || []
                const outOfScopeToShow = activeStage === "scope" ? (draftOutOfScope[activeStage] || []) : []
                const isFinalized = (finalizedContent[activeStage]?.length ?? 0) > 0
                const isDraft = !isFinalized && contentToShow.length > 0
                const canContinue = isFinalized && !allStagesComplete() && getNextIncompleteStage() !== activeStage

                return (
                    <>
                        {/* Stage Header */}
                        <div className="flex items-center justify-between gap-2 mb-2">
                            <div className="flex items-center gap-3">
                                <div className={`w-8 h-8 rounded-full flex items-center justify-center ${isFinalized ? "bg-[#78a530]" : "bg-gray-400"}`}>
                                    <span className="text-white text-sm font-bold">{isFinalized ? "✓" : stageOrder.indexOf(activeStage) + 1}</span>
                                </div>
                                <h2 className="text-xl font-bold text-gray-900">{getStageLabel(activeStage)}</h2>
                            </div>
                            {uploadedDocs.length > 0 && (
                                <div className="relative" ref={docsPopoverRef}>
                                    <button
                                        onClick={() => setShowDocsPopover(prev => !prev)}
                                        className="px-3 py-1.5 text-sm border border-gray-200 rounded-lg text-gray-600 hover:border-[#78a530] hover:text-[#78a530] transition"
                                    >
                                        Source Documents ({uploadedDocs.length})
                                    </button>
                                    {showDocsPopover && (
                                        <div className="absolute right-0 mt-2 w-80 bg-white border border-gray-200 rounded-xl shadow-lg p-4 z-20">
                                            <h4 className="text-sm font-semibold text-gray-800 mb-3">Source Documents</h4>
                                            <div className="space-y-2 max-h-64 overflow-auto">
                                                {uploadedDocs.map(doc => (
                                                    <div key={doc.file_id} className="flex items-center gap-3 p-2 bg-gray-50 rounded-lg">
                                                        <div className="w-8 h-8 bg-white border border-gray-200 rounded-lg flex items-center justify-center">
                                                            <span className="text-xs text-gray-600">{doc.file_type.toUpperCase()}</span>
                                                        </div>
                                                        <div className="min-w-0">
                                                            <p className="text-sm text-gray-800 truncate">{doc.filename}</p>
                                                            <p className="text-xs text-gray-500">{doc.file_type.toUpperCase()}</p>
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>

                        {/* Items as Cards */}
                        <div className="space-y-4 mb-6">
                            {activeStage === "feature" ? (
                                <>
                                    {(contentToShow || []).map((feature, idx) => (
                                        <div key={idx} className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:shadow-md transition group">
                                            {/* Feature Header */}
                                            <div className="flex items-start justify-between gap-4 mb-3">
                                                <div className="flex items-start gap-4 flex-1">
                                                    <div className="w-9 h-9 bg-gray-100 rounded-full flex items-center justify-center flex-shrink-0">
                                                        <span className="text-gray-700 text-sm font-bold">{idx + 1}</span>
                                                    </div>
                                                    <div className="flex-1">
                                                        <p className="text-base font-semibold text-gray-900">{feature}</p>
                                                    </div>
                                                    <button
                                                        onClick={() => {
                                                            setDeletePointIndex(idx)
                                                            setShowDeleteConfirm(true)
                                                        }}
                                                        className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700 p-1"
                                                        title="Delete Feature"
                                                    >
                                                        <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                        </svg>
                                                    </button>
                                                </div>

                                                {/* Action Buttons on Right */}
                                                <div className="flex-shrink-0 flex gap-2">
                                                    {!featureTasks[feature] || featureTasks[feature].length === 0 ? (
                                                        <>
                                                            <button
                                                                onClick={() => handleAutoGenerateTasks(feature)}
                                                                disabled={isGeneratingTasks}
                                                                className="px-3 py-1.5 text-xs bg-[#78a530] text-white rounded-md hover:bg-[#6b9429] transition disabled:opacity-50 disabled:cursor-not-allowed"
                                                            >
                                                                {isGeneratingTasks ? "Generating..." : "Generate Tasks with AI"}
                                                            </button>
                                                            <button
                                                                onClick={() => {
                                                                    setSelectedFeature(feature)
                                                                    setShowTaskGenModal(true)
                                                                }}
                                                                className="px-3 py-1.5 text-xs border border-gray-300 text-gray-700 rounded-md hover:border-[#78a530] hover:text-[#78a530] transition"
                                                            >
                                                                Brainstorm
                                                            </button>
                                                        </>
                                                    ) : (
                                                        <>
                                                            <button
                                                                onClick={() => {
                                                                    setSelectedFeature(feature)
                                                                    setShowAddTaskModal(true)
                                                                }}
                                                                className="px-3 py-1.5 text-xs bg-[#78a530] text-white rounded-md hover:bg-[#6b9429] transition"
                                                            >
                                                                + Add Task
                                                            </button>
                                                            <button
                                                                onClick={() => {
                                                                    setSelectedFeature(feature)
                                                                    setShowTaskGenModal(true)
                                                                }}
                                                                className="px-3 py-1.5 text-xs border border-gray-300 text-gray-700 rounded-md hover:border-[#78a530] hover:text-[#78a530] transition"
                                                            >
                                                                Revise Tasks
                                                            </button>
                                                            <button
                                                                onClick={() => handleClearTasks(feature)}
                                                                className="px-3 py-1.5 text-xs border border-red-300 text-red-600 rounded-md hover:bg-red-50 transition"
                                                            >
                                                                Clear All
                                                            </button>
                                                        </>
                                                    )}
                                                </div>
                                            </div>

                                            {/* Tasks List */}
                                            {featureTasks[feature] && featureTasks[feature].length > 0 && (
                                                <div className="ml-16 mt-3 space-y-2">
                                                    <p className="text-xs font-semibold text-gray-600 mb-2">Tasks:</p>
                                                    {featureTasks[feature].map((task, taskIdx) => (
                                                        <div key={taskIdx} className="flex items-start gap-2 text-sm text-gray-700 group">
                                                            <span className="text-[#78a530] mt-0.5">•</span>
                                                            <span className="flex-1">{task}</span>
                                                            <button
                                                                onClick={() => handleDeleteTask(feature, taskIdx)}
                                                                className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700"
                                                                title="Delete task"
                                                            >
                                                                <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                                </svg>
                                                            </button>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </>
                            ) : activeStage === "goal" ? (
                                /* Goal shows as single statement without numbering */
                                <>
                                    {(contentToShow || []).map((item, idx) => (
                                        <div key={idx} className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:shadow-md transition group">
                                            <div className="flex items-start gap-4">
                                                <p className="text-base text-gray-800 flex-1">{item}</p>
                                                <button
                                                    onClick={() => {
                                                        setDeletePointIndex(idx)
                                                        setShowDeleteConfirm(true)
                                                    }}
                                                    className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700 p-1"
                                                    title="Delete"
                                                >
                                                    <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                    </svg>
                                                </button>
                                            </div>
                                        </div>
                                    ))}
                                </>
                            ) : activeStage === "scope" ? (
                                <>
                                    <div className="space-y-6">
                                        <div>
                                            <div className="flex items-center gap-2 mb-3">
                                                <div className="w-7 h-7 bg-[#78a530]/10 rounded-full flex items-center justify-center">
                                                    <span className="text-[#78a530] text-xs font-bold">✓</span>
                                                </div>
                                                <h3 className="text-sm font-semibold text-gray-700">In Scope</h3>
                                            </div>
                                            {contentToShow.length > 0 ? (
                                                <div className="space-y-4">
                                                    {contentToShow.map((item, idx) => (
                                                        <div key={`scope-${idx}`} className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:shadow-md transition group">
                                                            <div className="flex items-start gap-4">
                                                                <div className="w-9 h-9 bg-gray-100 rounded-full flex items-center justify-center flex-shrink-0">
                                                                    <span className="text-gray-700 text-sm font-bold">{idx + 1}</span>
                                                                </div>
                                                                <p className="text-base text-gray-800 flex-1 pt-2">{item}</p>
                                                                <button
                                                                    onClick={() => {
                                                                        setDeletePointIndex(idx)
                                                                        setShowDeleteConfirm(true)
                                                                    }}
                                                                    className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700 p-1"
                                                                    title="Delete"
                                                                >
                                                                    <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                                    </svg>
                                                                </button>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <p className="text-sm text-gray-500">No in-scope items yet.</p>
                                            )}
                                        </div>

                                        <div>
                                            <div className="flex items-center gap-2 mb-3">
                                                <div className="w-7 h-7 bg-rose-100 rounded-full flex items-center justify-center">
                                                    <span className="text-rose-700 text-xs font-bold">!</span>
                                                </div>
                                                <h3 className="text-sm font-semibold text-rose-600">Out of Scope</h3>
                                            </div>
                                            {outOfScopeToShow.length > 0 ? (
                                                <div className="space-y-4">
                                                    {outOfScopeToShow.map((item, idx) => (
                                                        <div key={`out-scope-${idx}`} className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:shadow-md transition group">
                                                            <div className="flex items-start gap-4">
                                                                <div className="w-9 h-9 bg-rose-50 rounded-full flex items-center justify-center flex-shrink-0">
                                                                    <span className="text-rose-700 text-sm font-bold">{idx + 1}</span>
                                                                </div>
                                                                <p className="text-base text-gray-800 flex-1 pt-2">{item}</p>
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <p className="text-sm text-gray-500">No out-of-scope items yet.</p>
                                            )}
                                        </div>
                                    </div>
                                </>
                            ) : (
                                /* Other stages show with numbering */
                                <>
                                    {(contentToShow || []).map((item, idx) => (
                                        <div key={idx} className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:shadow-md transition group">
                                            <div className="flex items-start gap-4">
                                                <div className="w-9 h-9 bg-gray-100 rounded-full flex items-center justify-center flex-shrink-0">
                                                    <span className="text-gray-700 text-sm font-bold">{idx + 1}</span>
                                                </div>
                                                <p className="text-base text-gray-800 flex-1 pt-2">{item}</p>
                                                <button
                                                    onClick={() => {
                                                        setDeletePointIndex(idx)
                                                        setShowDeleteConfirm(true)
                                                    }}
                                                    className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700 p-1"
                                                    title="Delete"
                                                >
                                                    <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                    </svg>
                                                </button>
                                            </div>
                                        </div>
                                    ))}
                                </>
                            )}
                        </div>

                        {/* Actions */}
                        <div className="flex justify-center gap-4">
                            <button
                                onClick={() => {
                                    if (!charterMode) {
                                        setCharterMode("brainstorm")
                                    }
                                    setMode("unified_chat")
                                }}
                                className="px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:border-[#78a530] hover:text-[#78a530] transition flex items-center gap-2"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                                    <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                                </svg>
                                Brainstorm
                            </button>

                            <button
                                onClick={() => setShowAddModal(true)}
                                className="px-4 py-2 text-sm border border-gray-200 rounded-lg text-gray-600 hover:border-[#78a530] hover:text-[#78a530] transition"
                            >
                                Add new {getStageLabel(activeStage).toLowerCase()}
                            </button>

                            {isDraft && (
                                <button
                                    onClick={finalizeDraftStage}
                                    className="px-4 py-2 text-sm bg-[#78a530] text-white rounded-lg font-medium hover:bg-[#6b9429] transition"
                                >
                                    Finalize
                                </button>
                            )}

                            {/* Show Continue button only if:
                    1. Not all stages are complete AND
                    2. There are incomplete stages after current stage */}
                            {canContinue && (
                                <button
                                    onClick={() => {
                                        const nextStage = getNextIncompleteStage()
                                        setActiveStage(nextStage)
                                        const immediateMode = resolveModeForStage(nextStage, charterMode, stages, finalizedContent)
                                        setMode(immediateMode)
                                        if (charterMode === "brainstorm" && immediateMode !== "stage_complete") {
                                            void checkHasMessages(nextStage).then((hasAnyMessages) => {
                                                if (activeStageRef.current !== nextStage) return
                                                if (hasAnyMessages) {
                                                    setMode("unified_chat")
                                                }
                                            })
                                        }
                                    }}
                                    className="px-4 py-2 text-sm bg-[#78a530] text-white rounded-lg font-medium hover:bg-[#6b9429] transition"
                                >
                                    Continue to {getStageLabel(getNextIncompleteStage())} →
                                </button>
                            )}
                        </div>
                    </>
                )
            })()}
        </div>
    )

    // Brainstorm/Chat View (Full Screen - Legacy)
    const renderBrainstormContent = () => (
        <div className="w-full relative" style={{ height: "calc(100vh - 180px)" }}>
            <ChatInterface
                projectId={projectId}
                stage={activeStage}
                mode="brainstorm"
                projectType={projectType}
                projectName={projectName}
                stageLabel={getStageLabel(activeStage)}
                onFinalize={handleFinalize}
                onClose={async () => {
                    if (finalizedContent[activeStage]?.length > 0) {
                        setMode("stage_complete")
                    } else {
                        // Check if user actually sent any messages
                        const hasUserMsgs = await checkHasUserMessages(activeStage)
                        if (hasUserMsgs) {
                            setMode("unified_chat")
                        } else {
                            setMode(getDefaultEntryMode())
                        }
                    }
                }}
                onDraftUpdate={() => refreshStageEntities(activeStage, { force: true })}
                isDarkMode={isDarkMode}
            />
        </div>
    )

    // Unified Chat View (60% Chat, 40% Draft Panel) - New sleek design
    const renderUnifiedChat = () => {
        const contentToShow = finalizedContent[activeStage]?.length > 0
            ? finalizedContent[activeStage]
            : draftContent[activeStage] || []
        const scopeOutOfScope = activeStage === "scope" ? (draftOutOfScope[activeStage] || []) : []
        const isDraft = !finalizedContent[activeStage]?.length
            && ((draftContent[activeStage]?.length || 0) > 0 || scopeOutOfScope.length > 0)
        const hasPanelContent = contentToShow.length > 0 || (activeStage === "scope" && scopeOutOfScope.length > 0)

        const handleDraftEdit = async (index: number, newValue: string) => {
            const updated = [...(draftContent[activeStage] || [])]
            updated[index] = newValue
            setDraftContent(prev => ({ ...prev, [activeStage]: updated }))
            await persistDraft(
                activeStage,
                updated,
                activeStage === "scope" ? (draftOutOfScope[activeStage] || []) : undefined
            )
        }

        const handleDraftDelete = async (index: number) => {
            const updated = [...(draftContent[activeStage] || [])]
            updated.splice(index, 1)
            setDraftContent(prev => ({ ...prev, [activeStage]: updated }))
            await persistDraft(
                activeStage,
                updated,
                activeStage === "scope" ? (draftOutOfScope[activeStage] || []) : undefined
            )
        }

        const handleDraftAdd = () => {
            setShowAddModal(true)
        }

        return (
            <div className="w-full flex gap-4 relative" style={{ height: "calc(100vh - 180px)" }}>
                {/* Left Panel - Chat Interface (full width if no content, else 60%) */}
                <div className={`relative rounded-2xl overflow-hidden ${hasPanelContent ? 'flex-[3]' : 'flex-1'} ${dm ? "glass-card-dark" : "glass-card"}`}
                    style={dm ? { background: "rgba(17,21,32,0.95)", border: "1px solid rgba(255,255,255,0.08)" } : undefined}
                >
                    <ChatInterface
                        projectId={projectId}
                        stage={activeStage}
                        mode="brainstorm"
                        projectType={projectType}
                        projectName={projectName}
                        stageLabel={getStageLabel(activeStage)}
                        onFinalize={handleFinalize}
                        onClose={() => {
                            const resolvedMode = getResolvedCharterMode()
                            if (resolvedMode === "document") {
                                setMode("extract_from_documents")
                            } else {
                                setMode("stage_complete")
                            }
                        }}
                        onGapsDetected={setCurrentGaps}
                        onDraftUpdate={() => refreshStageEntities(activeStage, { force: true })}
                        isDarkMode={isDarkMode}
                    />
                </div>

                {/* Right Panel - Draft Panel (40%) - Only show when content exists */}
                {hasPanelContent && (
                    <div className={`flex-[2] rounded-2xl p-5 ${dm ? "glass-card-dark" : "glass-card"}`}
                        style={dm ? { background: "rgba(30,38,56,0.9)", border: "1px solid rgba(255,255,255,0.08)" } : undefined}
                    >
                        <DraftPanel
                            title={getStageLabel(activeStage)}
                            items={contentToShow}
                            secondaryTitle={activeStage === "scope" ? "Out of Scope" : undefined}
                            secondaryItems={activeStage === "scope" ? scopeOutOfScope : undefined}
                            gaps={currentGaps}
                            isDraft={isDraft}
                            isGoal={activeStage === "goal"}
                            onAddItem={handleDraftAdd}
                            onEditItem={handleDraftEdit}
                            onDeleteItem={handleDraftDelete}
                            onEditSecondaryItem={activeStage === "scope"
                                ? async (index, newValue) => {
                                    const updatedOut = [...(draftOutOfScope[activeStage] || [])]
                                    updatedOut[index] = newValue
                                    setDraftOutOfScope(prev => ({ ...prev, [activeStage]: updatedOut }))
                                    await persistDraft(activeStage, draftContent[activeStage] || [], updatedOut)
                                }
                                : undefined
                            }
                            onDeleteSecondaryItem={activeStage === "scope"
                                ? async (index) => {
                                    const updatedOut = [...(draftOutOfScope[activeStage] || [])]
                                    updatedOut.splice(index, 1)
                                    setDraftOutOfScope(prev => ({ ...prev, [activeStage]: updatedOut }))
                                    await persistDraft(activeStage, draftContent[activeStage] || [], updatedOut)
                                }
                                : undefined
                            }
                            onFinalize={finalizeDraftStage}
                            showFinalize={contentToShow.length > 0}
                        />
                    </div>
                )}

                {showKickoff && (
                    <div className={`absolute inset-0 flex items-center justify-center z-20 rounded-2xl ${dm ? "bg-[#111520]/95" : "bg-white/95"}`}>
                        <div className="max-w-2xl text-center px-6">
                            <div className="inline-flex items-center justify-center w-14 h-14 bg-gradient-to-br from-[#78a530] to-[#5a7d24] rounded-2xl mb-4 shadow-lg">
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-7 w-7 text-white" viewBox="0 0 24 24" fill="currentColor">
                                    <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                                </svg>
                            </div>
                            <h2 className={`text-2xl font-bold mb-2 ${dm ? "text-gray-100" : "text-gray-900"}`}>Let’s set up your Project Charter</h2>
                            <p className={`text-base ${dm ? "text-gray-400" : "text-gray-600"}`}>Let’s shape your Project Charter — starting with the goal.</p>
                        </div>
                    </div>
                )}
            </div>
        )
    }

    // Split Screen Chat View (Chat on Left, Finalized/Draft List on Right)
    const renderSplitScreenChat = () => {
        // Determine what content to show (finalized or draft)
        const contentToShow = finalizedContent[activeStage]?.length > 0
            ? finalizedContent[activeStage]
            : draftContent[activeStage] || []
        const outOfScopeToShow = activeStage === "scope" ? (draftOutOfScope[activeStage] || []) : []
        const isDraft = !finalizedContent[activeStage]?.length
            && ((draftContent[activeStage]?.length || 0) > 0 || outOfScopeToShow.length > 0)

        return (
            <div className="w-full flex gap-6" style={{ height: "calc(100vh - 180px)" }}>
                {/* Left Panel - Chat Interface */}
                <div className="flex-1 relative">
                    <ChatInterface
                        projectId={projectId}
                        stage={activeStage}
                        mode="brainstorm"
                        projectType={projectType}
                        projectName={projectName}
                        stageLabel={getStageLabel(activeStage)}
                        onFinalize={handleFinalize}
                        onClose={() => {
                            const resolvedMode = getResolvedCharterMode()
                            if (resolvedMode === "document") {
                                setMode("extract_from_documents")
                            } else {
                                setMode("stage_complete")
                            }
                        }}
                        onGapsDetected={setCurrentGaps}
                        onDraftUpdate={() => refreshStageEntities(activeStage, { force: true })}
                        isDarkMode={isDarkMode}
                    />
                </div>

                {/* Right Panel - Current List + Save Button */}
                <div className="w-96 flex-shrink-0 flex flex-col">
                    <div className="flex-1 bg-gray-50 rounded-xl p-4 overflow-y-auto mb-4">
                        <div className="flex items-center gap-2 mb-4">
                            <div className={`w-8 h-8 rounded-full flex items-center justify-center ${isDraft ? 'bg-gray-400' : 'bg-[#78a530]'}`}>
                                <span className="text-white text-sm">✓</span>
                            </div>
                            <h3 className={`text-base font-bold ${isDraft ? 'text-gray-700' : 'text-gray-900'}`}>
                                {getStageLabel(activeStage)} {isDraft && <span className="text-xs font-normal text-gray-500">(Draft)</span>}
                            </h3>
                        </div>

                        {contentToShow.length > 0 ? (
                            <div className="space-y-2">
                                {contentToShow.map((item, idx) => (
                                    <div key={idx} className="bg-white rounded-lg border border-gray-200 p-3 shadow-sm">
                                        <div className="flex items-start gap-2">
                                            <div className="w-6 h-6 bg-gray-100 rounded-full flex items-center justify-center flex-shrink-0">
                                                <span className="text-gray-700 text-xs font-bold">{idx + 1}</span>
                                            </div>
                                            <p className="text-xs text-gray-800 flex-1 pt-1">{item}</p>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="text-sm text-gray-500">No content yet. Chat to define your {getStageLabel(activeStage).toLowerCase()}!</p>
                        )}

                        {activeStage === "scope" && (
                            <div className="mt-4 pt-4 border-t border-dashed border-gray-200">
                                <div className="flex items-center gap-2 mb-3">
                                    <div className="w-6 h-6 bg-rose-100 rounded-full flex items-center justify-center">
                                        <span className="text-rose-700 text-xs font-bold">!</span>
                                    </div>
                                    <span className="text-xs font-semibold text-rose-600">Out of Scope</span>
                                </div>
                                {outOfScopeToShow.length > 0 ? (
                                    <div className="space-y-2">
                                        {outOfScopeToShow.map((item, idx) => (
                                            <div key={`out-${idx}`} className="bg-white rounded-lg border border-gray-200 p-3 shadow-sm">
                                                <div className="flex items-start gap-2">
                                                    <div className="w-6 h-6 bg-rose-50 rounded-full flex items-center justify-center flex-shrink-0">
                                                        <span className="text-rose-700 text-xs font-bold">{idx + 1}</span>
                                                    </div>
                                                    <p className="text-xs text-gray-800 flex-1 pt-1">{item}</p>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <p className="text-xs text-gray-400">No out-of-scope items yet.</p>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Finalize Button */}
                    <button
                        onClick={async () => {
                            // Extract and finalize current content from chat
                            try {
                                const content = draftContent[activeStage] || []
                                const outOfScope = activeStage === "scope"
                                    ? (draftOutOfScope[activeStage] || [])
                                    : undefined
                                if (content.length === 0) {
                                    return
                                }

                                const finalizeRes = await backendFetch(`${backendUrl}/api/planner/${projectId}/finalize/${activeStage}`, {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ content, out_of_scope: outOfScope })
                                })
                                if (finalizeRes.ok) {
                                    handleFinalize(content)
                                    // Just close and show stage_complete, no modal
                                    setMode("stage_complete")
                                } else {
                                    console.error("Failed to finalize content")
                                }
                            } catch (error) {
                                console.error("Error saving:", error)
                            }
                        }}
                        className="w-full py-3 bg-blue-500 text-white rounded-xl font-semibold hover:bg-blue-600 transition"
                    >
                        Finalize
                    </button>
                </div>
            </div>
        )
    }

    // Document Extraction View
    const renderExtractFromDocuments = () => (
        <div className="w-full relative" style={{ height: "calc(100vh - 180px)", padding: "24px" }}>
            <DocumentExtractor
                projectId={projectId}
                activeStage={activeStage}
                stages={stages}
                stageOrder={stageOrder}
                stageLabels={stageLabelMap}
                stageItems={draftContent[activeStage] || []}
                stageOutOfScopeItems={activeStage === "scope" ? (draftOutOfScope[activeStage] || []) : []}
                onExtracted={async () => {
                    clearProjectGlobalCaches(projectId)
                    stageEntityCacheRef.current.clear()
                    stageEntityInFlightRef.current.clear()
                    await refreshStatus(stageOrder)
                    await refreshStageEntities(activeStage, { force: true })
                    await refreshUploadedDocuments()
                }}
                onFinalize={async (content, outOfScope) => {
                    // Save finalized content to backend
                    try {
                        const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/finalize/${activeStage}`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ content, out_of_scope: outOfScope })
                        })

                        if (response.ok) {
                            // Update local state
                            setFinalizedContent(prev => ({ ...prev, [activeStage]: content }))
                            setStages(prev => ({ ...prev, [activeStage]: "finalized" }))
                        } else {
                            console.error("Failed to save finalized content")
                            alert("Failed to finalize content. Please try again.")
                        }
                    } catch (error) {
                        console.error("Error saving finalized content:", error)
                        alert("Error finalizing content. Please try again.")
                    }
                }}
                onClose={() => {
                    // Close document extractor
                    if (documentViewMode === "upload" && !hasDraftOrFinal) {
                        setCharterMode(null)
                        setMode("welcome")
                        return
                    }
                    if (finalizedContent[activeStage]?.length > 0) {
                        setMode("stage_complete")
                    } else {
                        setMode(getDefaultEntryMode())
                    }
                }}
                onContinueToNext={(nextStage: string) => {
                    // Navigate to next stage - stay in extract_from_documents mode
                    setActiveStage(nextStage as Stage)
                    // Mode stays as "extract_from_documents" - user can see extracted list
                    // User can click "Brainstorm" button if they want to chat
                }}
                onViewModeChange={setDocumentViewMode}
                hasDraftContent={hasDraftOrFinal}
                uploadedDocs={uploadedDocs}
                onDocumentsChanged={refreshUploadedDocuments}
                isDarkMode={isDarkMode}
            />
        </div>
    )

    // Check if we should show topic navigation
    const hasDraftOrFinal =
        (finalizedContent[activeStage]?.length ?? 0) > 0 ||
        (draftContent[activeStage]?.length ?? 0) > 0 ||
        (activeStage === "scope" && (draftOutOfScope[activeStage]?.length ?? 0) > 0)
    const hideDocUploadNav = charterMode === "document"
        && mode === "extract_from_documents"
        && documentViewMode === "upload"
        && !hasDraftOrFinal
    const showTopicNav = mode !== "welcome" && !hideDocUploadNav && !showKickoff
    const navTopics = activeTopics.map(topic => ({
        topic_id: topic.topic_id,
        stage_id: mapTopicToStageId(topic.topic_id),
        name: topic.name,
        group_id: topic.group_id,
        status: getStatusForStage(mapTopicToStageId(topic.topic_id))
    }))
    const isPlannerLoading = !modeLoaded

    const minimalTopics = [
        {
            topic_id: "goal",
            stage_id: "goal",
            name: "Goal",
            group_id: "core",
            status: getStatusForStage("goal")
        },
        ...(activeStage !== "goal"
            ? [{
                topic_id: activeStage,
                stage_id: activeStage,
                name: getStageLabel(activeStage),
                group_id: "core",
                status: getStatusForStage(activeStage)
            }]
            : [])
    ]
    const filteredNavTopics = charterMode === "brainstorm"
        ? navTopics.filter(topic => {
            const status = topic.status
            if (topic.stage_id === activeStage) return true
            if (status === "finalized" || status === "in_progress") return true
            return false
        })
        : navTopics
    const topicsForNav = charterMode === "brainstorm"
        ? (topicsLoaded ? filteredNavTopics : minimalTopics)
        : navTopics


    return (
        <div className="max-w-7xl mx-auto">
            {isPlannerLoading && (
                renderCharterLoading()
            )}
            {!isPlannerLoading && (
                <>
                    {/* Back to Welcome Button */}
                    {mode !== "welcome" && (
                        <div className="mb-4">
                            <button
                                onClick={handleBackToWelcome}
                                className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition group"
                                title="Return to welcome screen"
                            >
                                <svg
                                    xmlns="http://www.w3.org/2000/svg"
                                    className="h-5 w-5 group-hover:-translate-x-0.5 transition-transform"
                                    fill="none"
                                    viewBox="0 0 24 24"
                                    stroke="currentColor"
                                >
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                                </svg>
                                <span className="font-medium">Back to Welcome</span>
                            </button>
                        </div>
                    )}

                    {showTopicNav && (
                        <div className="relative">
                            <TopicNav
                                groups={topicGroups.length > 0 ? topicGroups : [{ id: "core", name: "Core", display_order: 1 }]}
                                topics={topicsForNav}
                                selectedGroupId={selectedGroupId || topicGroups[0]?.id || "core"}
                                activeStage={activeStage}
                                onSelectGroup={setSelectedGroupId}
                                onSelectStage={handleStageChange}
                                onAddTopic={() => setShowAddTopicModal(true)}
                                isDarkMode={isDarkMode}
                                rightActions={
                                    (mode as string) !== "welcome" ? (
                                        <button
                                            onClick={() => setShowResetConfirm(true)}
                                            className="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-500 hover:text-orange-600 hover:bg-orange-50 rounded-lg transition"
                                            title="Start Fresh - Reset all charter data"
                                        >
                                            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 0 01-15.357-2m15.357 2H15" />
                                            </svg>
                                            Start Fresh
                                        </button>
                                    ) : null
                                }
                            />
                            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                                <button
                                    type="button"
                                    title="Auto generate tasks based on Project Charter"
                                    className="pointer-events-auto inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold text-white bg-[#2563eb] hover:bg-[#1d4ed8] shadow-sm transition"
                                >
                                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                                        <path fillRule="evenodd" d="M9 4.5a.75.75 0 01.721.544l.813 2.846a3.75 3.75 0 002.576 2.576l2.846.813a.75.75 0 010 1.442l-2.846.813a3.75 3.75 0 00-2.576 2.576l-.813 2.846a.75.75 0 01-1.442 0l-.813-2.846a3.75 3.75 0 00-2.576-2.576l-2.846-.813a.75.75 0 010-1.442l2.846-.813A3.75 3.75 0 007.466 7.89l.813-2.846A.75.75 0 019 4.5zM18 1.5a.75.75 0 01.728.568l.258 1.036c.236.94.97 1.674 1.91 1.91l1.036.258a.75.75 0 010 1.456l-1.036.258c-.94.236-1.674.97-1.91 1.91l-.258 1.036a.75.75 0 01-1.456 0l-.258-1.036a2.625 2.625 0 00-1.91-1.91l-1.036-.258a.75.75 0 010-1.456l1.036-.258a2.625 2.625 0 001.91-1.91l.258-1.036A.75.75 0 0118 1.5zM16.5 15a.75.75 0 01.712.513l.394 1.183c.15.447.5.799.948.948l1.183.395a.75.75 0 010 1.422l-1.183.395c-.447.15-.799.5-.948.948l-.395 1.183a.75.75 0 01-1.422 0l-.395-1.183a1.5 1.5 0 00-.948-.948l-1.183-.395a.75.75 0 010-1.422l1.183-.395c.447-.15.799-.5.948-.948l.395-1.183A.75.75 0 0116.5 15z" clipRule="evenodd" />
                                    </svg>
                                    Generate Tasks
                                </button>
                            </div>
                        </div>
                    )}


                    <div className={showTopicNav ? "mt-0" : ""}>
                        {mode === "welcome" && renderWelcomeContent()}
                        {mode === "in_progress" && renderInProgressContent()}
                        {mode === "stage_complete" && renderStageCompleteContent()}
                        {mode === "brainstorm" && renderBrainstormContent()}
                        {mode === "split_screen_chat" && renderSplitScreenChat()}
                        {mode === "unified_chat" && renderUnifiedChat()}
                        {mode === "extract_from_documents" && renderExtractFromDocuments()}
                    </div>
                </>
            )}

            {/* Add Topic Modal */}
            {showAddTopicModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-2xl p-6 w-[520px] max-w-[92%] shadow-xl">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-10 h-10 bg-[#78a530] rounded-full flex items-center justify-center">
                                <span className="text-white text-lg">+</span>
                            </div>
                            <div>
                                <h3 className="text-xl font-bold text-gray-900">Add Topic</h3>
                                <p className="text-sm text-gray-500">Pick a topic to add to this project.</p>
                            </div>
                        </div>

                        <div className="max-h-[340px] overflow-y-auto pr-1">
                            {topicGroups.map(group => {
                                const groupTopics = optionalTopics
                                    .filter(topic => topic.group_id === group.id)
                                    .sort((a, b) => (a.display_order ?? 99) - (b.display_order ?? 99))

                                if (groupTopics.length === 0) return null

                                return (
                                    <div key={group.id} className="mb-4">
                                        <h4 className="text-sm font-semibold text-gray-800 mb-2">{group.name}</h4>
                                        <div className="space-y-2">
                                            {groupTopics.map(topic => (
                                                <div key={topic.id} className="flex items-start justify-between gap-3 border border-gray-200 rounded-lg p-3">
                                                    <div>
                                                        <div className="text-sm font-medium text-gray-900">{topic.name}</div>
                                                        {topic.description && (
                                                            <div className="text-xs text-gray-500 mt-1">{topic.description}</div>
                                                        )}
                                                    </div>
                                                    <button
                                                        onClick={() => handleActivateTopic(topic)}
                                                        disabled={isActivatingTopic}
                                                        className="px-3 py-1.5 bg-[#78a530] text-white text-xs font-semibold rounded-md hover:bg-[#6b9429] transition disabled:opacity-50 disabled:cursor-not-allowed"
                                                    >
                                                        {isActivatingTopic ? "Adding..." : "Add"}
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )
                            })}

                            {optionalTopics.length === 0 && (
                                <div className="text-sm text-gray-500">No additional topics available.</div>
                            )}
                        </div>

                        <div className="flex justify-end gap-3 mt-4">
                            <button
                                onClick={() => setShowAddTopicModal(false)}
                                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition text-sm"
                            >
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Add Point Modal */}
            {showAddModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-2xl p-6 w-[500px] max-w-[90%] shadow-xl">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-10 h-10 bg-[#78a530] rounded-full flex items-center justify-center">
                                <span className="text-white text-lg">+</span>
                            </div>
                            <h3 className="text-xl font-bold text-gray-900">
                                Add New {getStageLabel(activeStage)}
                            </h3>
                        </div>
                        <p className="text-sm text-gray-600 mb-4">
                            Enter a new {getStageLabel(activeStage).toLowerCase()} to add to your list.
                        </p>

                        <textarea
                            value={newPointText}
                            onChange={(e) => setNewPointText(e.target.value)}
                            placeholder={`e.g., ${activeStage === 'goal' ? 'Increase user engagement by 50%' : 'New ' + getStageLabel(activeStage).toLowerCase()}`}
                            rows={3}
                            className="w-full border border-gray-200 rounded-lg px-4 py-3 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent resize-none"
                            autoFocus
                        />

                        <div className="flex justify-end gap-3 mt-4">
                            <button
                                onClick={() => {
                                    setShowAddModal(false)
                                    setNewPointText("")
                                }}
                                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition text-sm"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleAddPoint}
                                disabled={!newPointText.trim() || isAddingPoint}
                                className="px-6 py-2 bg-[#78a530] text-white rounded-lg font-medium hover:bg-[#6b9429] transition disabled:opacity-50 disabled:cursor-not-allowed text-sm"
                            >
                                {isAddingPoint ? "Adding..." : "Add"}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Delete Confirmation Modal */}
            {showDeleteConfirm && deletePointIndex !== null && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-2xl p-6 w-[450px] max-w-[90%] shadow-xl">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-10 h-10 bg-red-500 rounded-full flex items-center justify-center">
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                </svg>
                            </div>
                            <h3 className="text-xl font-bold text-gray-900">Delete {getStageLabel(activeStage)}?</h3>
                        </div>
                        <p className="text-sm text-gray-600 mb-2">
                            Are you sure you want to delete this {getStageLabel(activeStage).toLowerCase()}?
                        </p>
                        <div className="bg-gray-50 rounded-lg p-3 mb-4">
                            <p className="text-sm text-gray-700 italic">
                                "{finalizedContent[activeStage]?.[deletePointIndex]}"
                            </p>
                        </div>
                        <p className="text-xs text-red-600 mb-4">This action cannot be undone.</p>

                        <div className="flex justify-end gap-3">
                            <button
                                onClick={() => {
                                    setShowDeleteConfirm(false)
                                    setDeletePointIndex(null)
                                }}
                                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition text-sm"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleDeletePoint}
                                disabled={isDeletingPoint}
                                className="px-6 py-2 bg-red-500 text-white rounded-lg font-medium hover:bg-red-600 transition disabled:opacity-50 disabled:cursor-not-allowed text-sm"
                            >
                                {isDeletingPoint ? "Deleting..." : "Delete"}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Reset Charter Confirmation Modal */}
            {showResetConfirm && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-2xl p-6 w-[450px] max-w-[90%] shadow-xl">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-10 h-10 bg-orange-500 rounded-full flex items-center justify-center">
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                                </svg>
                            </div>
                            <h3 className="text-xl font-bold text-gray-900">Start Fresh?</h3>
                        </div>
                        <p className="text-sm text-gray-600 mb-4">
                            This will erase all conversations and clear any captured project information. You'll return to the welcome screen.
                        </p>
                        <p className="text-xs text-orange-600 mb-4 font-medium">⚠️ This action cannot be undone.</p>

                        <div className="flex justify-end gap-3">
                            <button
                                onClick={() => setShowResetConfirm(false)}
                                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition text-sm"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleResetCharter}
                                disabled={isResetting}
                                className="px-6 py-2 bg-orange-500 text-white rounded-lg font-medium hover:bg-orange-600 transition disabled:opacity-50 disabled:cursor-not-allowed text-sm"
                            >
                                {isResetting ? "Resetting..." : "Start Fresh"}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Revision Chat Overlay */}
            {revisionMode && (
                <RevisionChatOverlay
                    projectId={projectId}
                    stage={activeStage}
                    finalizedContent={finalizedContent[activeStage] || []}
                    stageLabels={stageLabelMap}
                    onPointUpdated={(pointIndex, newContent) => {
                        // Update the finalized content in state
                        setFinalizedContent(prev => {
                            const updated = [...(prev[activeStage] || [])]
                            updated[pointIndex] = newContent
                            return { ...prev, [activeStage]: updated }
                        })
                        setRevisionMode(false)
                    }}
                    onClose={() => setRevisionMode(false)}
                />
            )}


            {/* Task Generation Modal */}
            {showTaskGenModal && selectedFeature && (
                <TaskGenModal
                    projectId={projectId}
                    feature={selectedFeature}
                    existingTasks={featureTasks[selectedFeature]}
                    onTasksGenerated={(tasks) => {
                        setFeatureTasks(prev => ({ ...prev, [selectedFeature]: tasks }))
                        setShowTaskGenModal(false)
                        setSelectedFeature(null)
                    }}
                    onClose={() => {
                        setShowTaskGenModal(false)
                        setSelectedFeature(null)
                    }}
                />
            )}

            {/* Add Task Modal */}
            {showAddTaskModal && selectedFeature && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-white rounded-2xl p-6 w-[500px] max-w-[90%] shadow-xl">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-10 h-10 bg-[#78a530] rounded-full flex items-center justify-center">
                                <span className="text-white text-lg">+</span>
                            </div>
                            <h3 className="text-xl font-bold text-gray-900">
                                Add Task to {selectedFeature}
                            </h3>
                        </div>
                        <p className="text-sm text-gray-600 mb-4">
                            Enter a new task for this feature. Include size estimate (S/M/L).
                        </p>

                        <textarea
                            value={newTaskText}
                            onChange={(e) => setNewTaskText(e.target.value)}
                            placeholder="e.g., Create API endpoint (Size: M)"
                            rows={3}
                            className="w-full border border-gray-200 rounded-lg px-4 py-3 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent resize-none"
                            autoFocus
                        />

                        <div className="flex justify-end gap-3 mt-4">
                            <button
                                onClick={() => {
                                    setShowAddTaskModal(false)
                                    setNewTaskText("")
                                    setSelectedFeature(null)
                                }}
                                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition text-sm"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleAddTask}
                                disabled={!newTaskText.trim()}
                                className="px-6 py-2 bg-[#78a530] text-white rounded-lg font-medium hover:bg-[#6b9429] transition disabled:opacity-50 disabled:cursor-not-allowed text-sm"
                            >
                                Add Task
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Template Selector Modal */}

        </div>
    )
}
