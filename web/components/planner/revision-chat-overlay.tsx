"use client"

import { useState, useEffect, useRef } from "react"
import { useSession } from "next-auth/react"

interface RevisionChatOverlayProps {
    projectId: string
    stage: string
    finalizedContent: string[]
    onPointUpdated: (pointIndex: number, newContent: string) => void
    onClose: () => void
    stageLabels?: Record<string, string>
}

interface ChatMessage {
    role: "user" | "assistant"
    content: string
}

export function RevisionChatOverlay({
    projectId,
    stage,
    finalizedContent,
    onPointUpdated,
    onClose,
    stageLabels
}: RevisionChatOverlayProps) {
    const [messages, setMessages] = useState<ChatMessage[]>([])
    const [inputValue, setInputValue] = useState("")
    const [isLoading, setIsLoading] = useState(false)
    const [isInitializing, setIsInitializing] = useState(true)
    const [isExtracting, setIsExtracting] = useState(false)
    const [selectedPointIndex, setSelectedPointIndex] = useState<number | null>(null)
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

    const displayStageLabel = stageLabels?.[stage] || stage

    // Scroll to bottom
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
    }, [messages])

    // Initialize revision session
    useEffect(() => {
        const initRevision = async () => {
            setIsInitializing(true)
            try {
                const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/start-revision/${stage}`, {
                    method: "POST"
                })

                if (response.ok) {
                    const data = await response.json()
                    if (data.initial_message) {
                        setMessages([{ role: "assistant", content: data.initial_message }])
                    }
                }
            } catch (error) {
                console.error("Error starting revision:", error)
            }
            setIsInitializing(false)
        }

        initRevision()
    }, [projectId, stage, backendUrl])

    const sendMessage = async () => {
        if (!inputValue.trim() || isLoading) return

        const userMessage = inputValue.trim()
        setInputValue("")

        setMessages(prev => [...prev, { role: "user", content: userMessage }])
        setIsLoading(true)

        try {
            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/revision-chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content: userMessage, stage })
            })

            if (response.ok) {
                const data = await response.json()
                console.log("Revision chat response:", data) // DEBUG
                setMessages(prev => [...prev, { role: "assistant", content: data.content }])

                // Update selected point index if returned
                if (data.point_index !== undefined && data.point_index !== null) {
                    console.log("Setting selected point index to:", data.point_index) // DEBUG
                    setSelectedPointIndex(data.point_index)
                } else {
                    console.log("No point_index in response") // DEBUG
                }
            } else {
                setMessages(prev => [...prev, {
                    role: "assistant",
                    content: "I apologize, something went wrong. Please try again."
                }])
            }
        } catch (error) {
            console.error("Error sending message:", error)
            setMessages(prev => [...prev, {
                role: "assistant",
                content: "Connection error. Please check your connection and try again."
            }])
        }

        setIsLoading(false)
        // Refocus input after sending
        setTimeout(() => inputRef.current?.focus(), 100)
    }

    const handleUpdatePoint = async () => {
        setIsExtracting(true)
        try {
            // First, try to extract the revised point
            // This endpoint will check if a point is selected on the backend
            const extractResponse = await backendFetch(`${backendUrl}/api/planner/${projectId}/extract-revised-point/${stage}`, {
                method: "POST"
            })

            if (!extractResponse.ok) {
                const errorData = await extractResponse.json()
                if (errorData.detail?.includes("No point selected")) {
                    alert("Please select which point you want to revise by mentioning its number (e.g., 'point 1' or 'the second one').")
                } else {
                    alert("Could not extract revised point. Please continue the discussion.")
                }
                setIsExtracting(false)
                return
            }

            const extractData = await extractResponse.json()
            const { revised_point, point_index } = extractData

            if (point_index === null || point_index === undefined) {
                alert("Please select which point you want to revise by mentioning its number (e.g., 'point 1' or 'the second one').")
                setIsExtracting(false)
                return
            }

            // Update the local state if we didn't have it
            if (selectedPointIndex === null) {
                setSelectedPointIndex(point_index)
            }

            // Update the point
            const updateResponse = await backendFetch(`${backendUrl}/api/planner/${projectId}/update-point/${stage}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content: [revised_point] })
            })

            if (updateResponse.ok) {
                onPointUpdated(point_index, revised_point)
                onClose()
            } else {
                alert("Failed to update point. Please try again.")
            }
        } catch (error) {
            console.error("Error updating point:", error)
            alert("Error updating point. Please try again.")
        }
        setIsExtracting(false)
    }

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            {/* Overlay Chat Window */}
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl h-[70vh] flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 bg-[#78a530] rounded-lg flex items-center justify-center">
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-white" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
                            </svg>
                        </div>
                        <div>
                            <h2 className="text-lg font-bold text-gray-900">Revising {displayStageLabel}</h2>
                            <p className="text-xs text-gray-500">
                                {selectedPointIndex !== null
                                    ? `Editing point ${selectedPointIndex + 1}: "${finalizedContent[selectedPointIndex]?.substring(0, 40)}..."`
                                    : "Discuss changes with Marshal"}
                            </p>
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        className="text-gray-400 hover:text-gray-600 transition"
                    >
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                {/* Messages Area */}
                <div className="flex-1 overflow-y-auto p-6">
                    {isInitializing ? (
                        <div className="flex items-center justify-center h-full">
                            <div className="flex items-center gap-2 text-gray-500">
                                <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                                <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                                <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                            </div>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {messages.map((message, index) => (
                                <div
                                    key={index}
                                    className={`flex gap-3 ${message.role === "assistant" ? "" : "flex-row-reverse"}`}
                                >
                                    {/* Avatar */}
                                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${message.role === "assistant"
                                        ? "bg-[#78a530] text-white"
                                        : "bg-gray-200 text-gray-600"
                                        }`}>
                                        {message.role === "assistant" ? (
                                            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                                                <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
                                            </svg>
                                        ) : (
                                            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                                                <path d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
                                            </svg>
                                        )}
                                    </div>
                                    {/* Content */}
                                    <div className={`flex-1 ${message.role === "user" ? "text-right" : ""}`}>
                                        <div className={`inline-block px-4 py-2 rounded-xl ${message.role === "assistant"
                                            ? "bg-gray-100 text-gray-800"
                                            : "bg-[#78a530] text-white"
                                            }`}>
                                            <p className="text-sm whitespace-pre-wrap leading-relaxed">{message.content}</p>
                                        </div>
                                    </div>
                                </div>
                            ))}

                            {isLoading && (
                                <div className="flex gap-3">
                                    <div className="w-8 h-8 rounded-lg bg-[#78a530] text-white flex items-center justify-center flex-shrink-0">
                                        <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                                            <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
                                        </svg>
                                    </div>
                                    <div className="flex items-center gap-1">
                                        <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                                        <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                                        <div className="w-2 h-2 bg-[#78a530] rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                                    </div>
                                </div>
                            )}
                            <div ref={messagesEndRef} />
                        </div>
                    )}
                </div>

                {/* Input Area */}
                <div className="border-t border-gray-200 p-4">
                    <div className="flex items-center gap-3">
                        <textarea
                            ref={inputRef}
                            value={inputValue}
                            onChange={(e) => setInputValue(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter" && !e.shiftKey) {
                                    e.preventDefault()
                                    sendMessage()
                                }
                            }}
                            placeholder="Message Marshal..."
                            rows={1}
                            className="flex-1 resize-none border border-gray-200 rounded-lg px-4 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent"
                            disabled={isLoading}
                        />
                        <button
                            onClick={sendMessage}
                            disabled={isLoading || !inputValue.trim()}
                            className="p-2 bg-[#78a530] text-white rounded-lg hover:bg-[#6b9429] transition disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                            </svg>
                        </button>
                        <button
                            onClick={handleUpdatePoint}
                            disabled={isExtracting || messages.length < 3}
                            className="px-4 py-2 bg-[#78a530] hover:bg-[#6b9429] text-white rounded-lg text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            {isExtracting ? "Updating..." : "Update Point"}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    )
}
