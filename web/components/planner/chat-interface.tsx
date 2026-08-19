"use client"

import { useState, useEffect, useRef } from "react"
import { useSession } from "next-auth/react"

const startSessionInFlight = new Map<string, Promise<any>>()
const messageCache = new Map<string, ChatMessage[]>()

export const clearChatMessageCache = () => {
    messageCache.clear()
}

interface ChatMessage {
    role: "user" | "assistant"
    content: string
    created_at?: string
}

const parseStructuredReply = (content: string): string | null => {
    const trimmed = content.trim()
    if (!trimmed) return null

    let jsonCandidate = trimmed
    if (trimmed.startsWith("```")) {
        const fencedMatch = trimmed.match(/```json\s*([\s\S]*?)```/i) || trimmed.match(/```\s*([\s\S]*?)```/)
        if (fencedMatch) {
            jsonCandidate = fencedMatch[1].trim()
        }
    }

    if (!jsonCandidate.startsWith("{") || !jsonCandidate.endsWith("}")) {
        return null
    }

    try {
        const parsed = JSON.parse(jsonCandidate)
        if (parsed && typeof parsed === "object") {
            if (typeof parsed.reply === "string") return parsed.reply
            if (typeof parsed.message === "string") return parsed.message
            if (typeof parsed.response === "string") return parsed.response
        }
    } catch {
        return null
    }

    return null
}

const normalizeHistoryMessages = (historyMessages: ChatMessage[]): ChatMessage[] => {
    return historyMessages.map(message => {
        if (message.role !== "assistant" || !message.content) {
            return message
        }

        const parsedReply = parseStructuredReply(message.content)
        if (!parsedReply) {
            return message
        }

        return {
            ...message,
            content: parsedReply
        }
    })
}

const normalizeAssistantMessage = (content: string): string => {
    const parsedReply = parseStructuredReply(content)
    return parsedReply || content
}

interface ChatInterfaceProps {
    projectId: string
    stage: string
    mode?: "brainstorm" | "document"
    projectType?: string
    projectName?: string
    stageLabel?: string
    onFinalize?: (content: string[], outOfScope?: string[]) => void
    onClose?: () => void
    onGapsDetected?: (gaps: string[]) => void
    onDraftUpdate?: (draft: string[]) => void
    stageLabels?: Record<string, string>
    isDarkMode?: boolean
}

export function ChatInterface({
    projectId,
    stage,
    mode = "brainstorm",
    projectType = "Software / Product Development",
    projectName = "",
    stageLabel,
    onFinalize,
    onClose,
    onGapsDetected,
    onDraftUpdate,
    stageLabels: stageLabelsProp,
    isDarkMode = false
}: ChatInterfaceProps) {
    const dm = isDarkMode
    const [messages, setMessages] = useState<ChatMessage[]>([])
    const [inputValue, setInputValue] = useState("")
    const [isLoading, setIsLoading] = useState(false)
    const [isInitializing, setIsInitializing] = useState(true)
    const [isFinalizing, setIsFinalizing] = useState(false)
    const [extractedContent, setExtractedContent] = useState<string[]>([])
    const [showConfirmModal, setShowConfirmModal] = useState(false)
    const [thinkingPhase, setThinkingPhase] = useState(0)
    const messagesEndRef = useRef<HTMLDivElement>(null)
    const inputRef = useRef<HTMLTextAreaElement>(null)
    const { data: session } = useSession()
    const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
    const backendToken = String(session?.user?.backendToken || "").trim()
    const backendFetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
        const headers = new Headers(init.headers || {})
        if (backendToken && !headers.has("Authorization")) {
            headers.set("Authorization", `Bearer ${backendToken}`)
        }
        return window.fetch(input, { ...init, headers })
    }
    const cacheKey = `${projectId}:${stage}:${mode}`

    // PM-specific thinking messages
    const thinkingMessages: Record<string, string[]> = {
        default: ["Thinking...", "Reviewing context...", "Drafting response..."]
    }

    // Scroll to bottom
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
    }, [messages])

    // Initialize session using /agent/start endpoint
    useEffect(() => {
        const controller = new AbortController()
        let isActive = true

        const initializeChat = async () => {
            const cached = messageCache.get(cacheKey)
            if (cached && cached.length > 0) {
                setMessages(cached)
                setIsInitializing(false)
                return
            }
            setMessages([])
            setIsInitializing(true)
            try {
                // First check for existing history
                const historyRes = await backendFetch(`${backendUrl}/api/planner/${projectId}/history/${stage}`, {
                    signal: controller.signal
                })
                if (historyRes.ok) {
                    const historyData = await historyRes.json()
                    if (historyData.messages && historyData.messages.length > 0) {
                        if (isActive) {
                            setMessages(normalizeHistoryMessages(historyData.messages))
                            setIsInitializing(false)
                        }
                        return
                    }
                }

                // No history - start new session with agent endpoint (idempotent across remounts)
                const sessionKey = `${projectId}:${stage}:${mode}:${projectType}`
                let startPromise = startSessionInFlight.get(sessionKey)
                if (!startPromise) {
                    startPromise = backendFetch(`${backendUrl}/api/agent/start`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        signal: controller.signal,
                        body: JSON.stringify({
                            project_id: projectId,
                            topic: stage,
                            mode: mode,
                            project_type: projectType,
                            project_name: projectName
                        })
                    })
                        .then(res => (res.ok ? res.json() : null))
                        .finally(() => startSessionInFlight.delete(sessionKey))
                    startSessionInFlight.set(sessionKey, startPromise)
                }

                const data = await startPromise
                if (isActive) {
                    if (data?.message) {
                        setMessages([{ role: "assistant", content: normalizeAssistantMessage(data.message) }])
                    }
                    // Handle draft if returned
                    if (data?.draft && data.draft.length > 0 && onDraftUpdate) {
                        onDraftUpdate(data.draft)
                    }
                    // Handle gaps if returned
                    if (data?.gaps && data.gaps.length > 0 && onGapsDetected) {
                        onGapsDetected(data.gaps)
                    }
                }
            } catch (error) {
                if (isActive && !(controller.signal.aborted)) {
                    console.error("Error initializing chat:", error)
                }
            }
            if (isActive) {
                setIsInitializing(false)
            }
        }

        initializeChat()
        return () => {
            isActive = false
            controller.abort()
        }
    }, [projectId, stage, mode, backendUrl, cacheKey])

    useEffect(() => {
        if (messages.length > 0) {
            messageCache.set(cacheKey, messages)
        }
    }, [messages, cacheKey])

    const sendMessage = async () => {
        if (!inputValue.trim() || isLoading) return

        const userMessage = inputValue.trim()
        setInputValue("")

        setMessages(prev => [...prev, { role: "user", content: userMessage }])
        setIsLoading(true)
        setThinkingPhase(0)

        // Cycle through thinking phases
        const phaseInterval = setInterval(() => {
            setThinkingPhase(prev => (prev + 1) % 3)
        }, 1500)

        try {
            // Use /agent/chat endpoint
            const response = await backendFetch(`${backendUrl}/api/agent/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: projectId,
                    topic: stage,
                    message: userMessage,
                    project_type: projectType
                })
            })

            clearInterval(phaseInterval)

            if (response.ok) {
                const data = await response.json()

                // Add assistant message
                if (data.message) {
                    setMessages(prev => [
                        ...prev,
                        { role: "assistant", content: normalizeAssistantMessage(data.message) }
                    ])
                }

                // Update draft panel if new items extracted
                if (data.draft && data.draft.length > 0 && onDraftUpdate) {
                    onDraftUpdate(data.draft)
                }

                // Handle gaps detected
                if (data.gaps && data.gaps.length > 0 && onGapsDetected) {
                    onGapsDetected(data.gaps)
                }

                // Handle action-based flow (ready_to_finalize, etc.)
                if (data.action === "ready_to_finalize" && data.draft) {
                    setExtractedContent(data.draft)
                    setShowConfirmModal(true)
                }
            } else {
                setMessages(prev => [...prev, {
                    role: "assistant",
                    content: "I apologize, something went wrong. Please try again."
                }])
            }
        } catch (error) {
            console.error("Error sending message:", error)
            clearInterval(phaseInterval)
            setMessages(prev => [...prev, {
                role: "assistant",
                content: "Connection error. Please check your connection and try again."
            }])
        }

        setIsLoading(false)
        setThinkingPhase(0)
        // Refocus input after sending
        setTimeout(() => inputRef.current?.focus(), 100)
    }

    const handleKeyPress = (e: React.KeyboardEvent) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault()
            sendMessage()
        }
    }

    const handleFinalizeClick = async () => {
        setIsFinalizing(true)
        try {
            // Use /agent/finalize for validation + finalization
            const response = await fetch(`${backendUrl}/api/agent/finalize`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: projectId,
                    topic: stage,
                    project_type: projectType
                })
            })

            if (response.ok) {
                const data = await response.json()

                if (data.action === "finalized") {
                    // Successfully finalized
                    if (onFinalize && data.draft) {
                        onFinalize(data.draft)
                    }
                } else if (data.action === "continue_chat") {
                    // Gaps found - show message in chat
                    if (data.message) {
                        setMessages(prev => [
                            ...prev,
                            { role: "assistant", content: normalizeAssistantMessage(data.message) }
                        ])
                    }
                    if (data.gaps && data.gaps.length > 0 && onGapsDetected) {
                        onGapsDetected(data.gaps)
                    }
                } else if (data.draft && data.draft.length > 0) {
                    // Show confirmation modal with draft
                    setExtractedContent(data.draft)
                    setShowConfirmModal(true)
                }
            } else {
                const error = await response.json()
                alert(error.detail || "Could not finalize. Please continue the discussion.")
            }
        } catch (error) {
            console.error("Error finalizing:", error)
            alert("Error finalizing content. Please try again.")
        }
        setIsFinalizing(false)
    }

    const handleConfirmFinalize = async () => {
        // When modal confirms, call finalize again to complete
        try {
            const response = await fetch(`${backendUrl}/agent/finalize`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: projectId,
                    topic: stage,
                    project_type: projectType
                })
            })

            if (response.ok) {
                setShowConfirmModal(false)
                if (onFinalize) {
                    onFinalize(extractedContent)
                }
            }
        } catch (error) {
            console.error("Error finalizing:", error)
        }
    }

    const displayStageLabel = stageLabel || stageLabelsProp?.[stage] || stage

    return (
        <div className="flex flex-col h-full relative">
            {/* Confirm Modal */}
            {showConfirmModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div
                        className={`rounded-2xl p-6 w-[550px] max-w-[90%] shadow-xl ${dm ? "text-gray-100" : "bg-white text-gray-900"}`}
                        style={dm ? {
                            background: "linear-gradient(135deg, rgba(30,38,56,0.95) 0%, rgba(17,21,32,0.98) 100%)",
                            backdropFilter: "blur(20px)",
                            WebkitBackdropFilter: "blur(20px)",
                            border: "1px solid rgba(255,255,255,0.18)",
                            boxShadow: "0 8px 40px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.12)",
                        } : undefined}
                    >
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-10 h-10 bg-[#78a530] rounded-full flex items-center justify-center">
                                <span className="text-white text-lg">✓</span>
                            </div>
                            <h3 className={`text-xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>
                                Confirm {displayStageLabel}
                            </h3>
                        </div>
                        <p className={`text-sm mb-4 ${dm ? "text-gray-400" : "text-gray-600"}`}>
                            Based on our discussion, here are your {displayStageLabel.toLowerCase()}:
                        </p>

                        <div className={`rounded-xl p-4 mb-6 max-h-64 overflow-y-auto ${dm ? "bg-white/[0.05]" : "bg-gray-50"}`}>
                            <ul className="space-y-3">
                                {extractedContent.map((item, index) => (
                                    <li key={index} className="flex items-start gap-3">
                                        <div className="w-6 h-6 bg-[#78a530] rounded-full flex items-center justify-center flex-shrink-0 mt-0.5">
                                            <span className="text-white text-xs font-bold">{index + 1}</span>
                                        </div>
                                        <p className={dm ? "text-gray-300" : "text-gray-800"}>{item}</p>
                                    </li>
                                ))}
                            </ul>
                        </div>

                        <div className="flex justify-end gap-3">
                            <button
                                onClick={() => setShowConfirmModal(false)}
                                className={`px-4 py-2 rounded-lg transition ${dm ? "text-gray-300 hover:bg-white/[0.08]" : "text-gray-600 hover:bg-gray-100"}`}
                            >
                                Edit More
                            </button>
                            <button
                                onClick={handleConfirmFinalize}
                                className="px-6 py-2 bg-[#78a530] text-white rounded-lg font-medium hover:bg-[#6b9429] transition"
                            >
                                Confirm & Finalize
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Messages Area - Claude-like style */}
            <div className="flex-1 overflow-y-auto pt-4 pb-32">
                {isInitializing && messages.length === 0 ? (
                    <div className="flex items-center justify-center h-32">
                        <div className="flex items-center gap-2 text-gray-500">
                            <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                            <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                            <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                        </div>
                    </div>
                ) : (
                    <div className="max-w-3xl mx-auto">
                        {messages.map((message, index) => (
                            <div
                                key={index}
                                className={`py-4 px-4 mb-3 mx-2 rounded-2xl streaming-text ${message.role === "assistant"
                                    ? "glass-bubble-marshal"
                                    : "glass-bubble-user ml-12"
                                    }`}
                            >
                                <div className="flex gap-4">
                                    {/* Avatar with Sparkle */}
                                    <div className={`w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0 ${message.role === "assistant"
                                        ? "bg-gradient-to-br from-[#78a530] to-[#5d8024] text-white marshal-sparkle"
                                        : dm ? "bg-white/[0.10] text-gray-400" : "bg-gradient-to-br from-gray-100 to-gray-200 text-gray-600"
                                        }`}>
                                        {message.role === "assistant" ? (
                                            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                                                <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                                            </svg>
                                        ) : (
                                            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                                                <path d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
                                            </svg>
                                        )}
                                    </div>
                                    {/* Content */}
                                    <div className="flex-1 min-w-0">
                                        {message.role === "assistant" && (
                                            <p className="text-xs font-semibold text-[#78a530] mb-1 flex items-center gap-1">
                                                <span>✦</span> Marshal
                                            </p>
                                        )}
                                        <div className={`text-base whitespace-pre-wrap leading-relaxed ${dm ? "text-gray-200" : "text-gray-800"}`}>
                                            {message.content}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ))}

                        {isLoading && (
                            <div className="py-5 px-4 glass-bubble-marshal mb-3 mx-4 thinking-sparkle">
                                <div className="flex gap-4">
                                    <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-[#78a530] to-[#5d8024] text-white flex items-center justify-center flex-shrink-0 marshal-sparkle">
                                        <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                                            <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                                        </svg>
                                    </div>
                                    <div className="flex-1">
                                        <p className="text-xs font-semibold text-[#78a530] mb-2 flex items-center gap-1">
                                            <span>✦</span> Marshal
                                        </p>
                                        <div className="flex items-center gap-2">
                                            <div className="typing-indicator">
                                                <span></span>
                                                <span></span>
                                                <span></span>
                                            </div>
                                            <span className={`text-base italic ${dm ? "text-gray-400" : "text-gray-600"}`}>
                                                {thinkingMessages.default[thinkingPhase % thinkingMessages.default.length]}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}
                        <div ref={messagesEndRef} />
                    </div>
                )
                }
            </div >

            {/* Floating Input Box - Simplified */}
            <div
                className="absolute bottom-0 left-0 right-0 pt-8 pb-4 px-4"
                style={{ background: dm ? "linear-gradient(to top, rgba(17,21,32,1) 60%, rgba(17,21,32,0))" : "linear-gradient(to top, white 60%, transparent)" }}
            >
                <div className="max-w-3xl mx-auto">
                    <div
                        className={`${dm ? "glass-card-dark" : "glass-card"} chat-input-shell overflow-hidden input-focus-glow transition-all duration-200`}
                        style={dm ? {
                            background: "linear-gradient(135deg, rgba(30,38,56,0.9) 0%, rgba(20,26,42,0.95) 100%)",
                            border: "1px solid rgba(255,255,255,0.12)",
                        } : undefined}
                    >
                        {/* Top bar with stage info only - no finalize button */}
                        <div className={`flex items-center justify-between px-4 py-2 border-b ${dm ? "border-white/[0.08] bg-white/[0.03]" : "border-gray-100 bg-gray-50"}`}>
                            <div className="flex items-center gap-2">
                                <div className="w-2 h-2 bg-[#78a530] rounded-full"></div>
                                <span className={`text-xs font-medium ${dm ? "text-gray-400" : "text-gray-600"}`}>Defining {displayStageLabel}</span>
                            </div>
                            {onClose && (
                                <button
                                    onClick={onClose}
                                    className={`text-xs transition ${dm ? "text-gray-500 hover:text-gray-300" : "text-gray-500 hover:text-gray-700"}`}
                                >
                                    Minimize
                                </button>
                            )}
                        </div>
                        {/* Input area with auto-expanding textarea */}
                        <div className="flex items-end gap-2 p-3">
                            <textarea
                                ref={inputRef}
                                value={inputValue}
                                onChange={(e) => {
                                    setInputValue(e.target.value)
                                    // Auto-expand textarea
                                    e.target.style.height = 'auto'
                                    e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px'
                                }}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" && !e.shiftKey) {
                                        e.preventDefault()
                                        sendMessage()
                                        // Reset height after sending
                                        if (inputRef.current) {
                                            inputRef.current.style.height = 'auto'
                                        }
                                    }
                                }}
                                placeholder="Message Marshal..."
                                rows={1}
                                className={`flex-1 resize-none border-0 bg-transparent text-base focus:outline-none focus:ring-0 overflow-hidden ${dm ? "text-gray-100 placeholder-gray-500" : "text-gray-800 placeholder-gray-400"}`}
                                style={{ minHeight: "24px", maxHeight: "200px" }}
                                disabled={isLoading}
                            />
                            <button
                                onClick={() => {
                                    sendMessage()
                                    // Reset height after sending
                                    if (inputRef.current) {
                                        inputRef.current.style.height = 'auto'
                                    }
                                }}
                                disabled={isLoading || !inputValue.trim()}
                                className="p-2 bg-[#78a530] text-white rounded-lg hover:bg-[#6b9429] transition disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
                            >
                                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                                    <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div >
    )
}
