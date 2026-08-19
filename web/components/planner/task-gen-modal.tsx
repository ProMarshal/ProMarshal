"use client"

import { useState, useEffect, useRef } from "react"
import { useSession } from "next-auth/react"

interface TaskGenModalProps {
    projectId: string
    feature: string
    existingTasks?: string[]
    onTasksGenerated: (tasks: string[]) => void
    onClose: () => void
}

interface ChatMessage {
    role: "user" | "assistant"
    content: string
}

export function TaskGenModal({ projectId, feature, existingTasks, onTasksGenerated, onClose }: TaskGenModalProps) {
    const [messages, setMessages] = useState<ChatMessage[]>([])
    const [inputValue, setInputValue] = useState("")
    const [isLoading, setIsLoading] = useState(false)
    const [isFinalizing, setIsFinalizing] = useState(false)
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

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
    }, [messages])

    useEffect(() => {
        const initChat = async () => {
            const initialMsg = existingTasks && existingTasks.length > 0
                ? `Let's revise tasks for "${feature}".\n\nCurrent tasks:\n${existingTasks.map((t, i) => `${i + 1}. ${t}`).join('\n')}\n\nWhat would you like to change?`
                : `Let's break down "${feature}" into tasks. What are the key things needed to implement this?`

            setMessages([{ role: "assistant", content: initialMsg }])
        }
        initChat()
    }, [feature, existingTasks])

    const sendMessage = async () => {
        if (!inputValue.trim() || isLoading) return

        const userMessage = inputValue.trim()
        setInputValue("")
        setMessages(prev => [...prev, { role: "user", content: userMessage }])
        setIsLoading(true)

        try {
            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/task-brainstorm`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    feature,
                    message: userMessage,
                    existing_tasks: existingTasks || []
                })
            })

            if (response.ok) {
                const data = await response.json()
                setMessages(prev => [...prev, { role: "assistant", content: data.content }])
            } else {
                setMessages(prev => [...prev, {
                    role: "assistant",
                    content: "I apologize, something went wrong. Please try again."
                }])
            }
        } catch (error) {
            console.error("Error:", error)
            setMessages(prev => [...prev, {
                role: "assistant",
                content: "Connection error. Please try again."
            }])
        }

        setIsLoading(false)
        setTimeout(() => inputRef.current?.focus(), 100)
    }

    const handleFinalize = async () => {
        setIsFinalizing(true)
        try {
            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/extract-tasks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ feature, messages })
            })

            if (response.ok) {
                const data = await response.json()
                onTasksGenerated(data.tasks)
                onClose()
            } else {
                alert("Could not extract tasks. Please continue the discussion.")
            }
        } catch (error) {
            console.error("Error:", error)
            alert("Error extracting tasks. Please try again.")
        }
        setIsFinalizing(false)
    }

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl h-[70vh] flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
                    <div>
                        <h2 className="text-lg font-bold text-gray-900">Task Brainstorm</h2>
                        <p className="text-xs text-gray-500">{feature}</p>
                    </div>
                    <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                {/* Messages */}
                <div className="flex-1 overflow-y-auto p-6">
                    <div className="space-y-4">
                        {messages.map((message, index) => (
                            <div key={index} className={`flex gap-3 ${message.role === "user" ? "flex-row-reverse" : ""}`}>
                                <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${message.role === "assistant" ? "bg-[#78a530] text-white" : "bg-gray-200 text-gray-600"}`}>
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
                                <div className={`flex-1 ${message.role === "user" ? "text-right" : ""}`}>
                                    {message.role === "assistant" && (
                                        <p className="text-xs font-medium text-gray-500 mb-1">Marshal</p>
                                    )}
                                    <div className={`inline-block px-4 py-2 rounded-xl ${message.role === "assistant" ? "bg-gray-100 text-gray-800" : "bg-[#78a530] text-white"}`}>
                                        <p className="text-sm whitespace-pre-wrap leading-relaxed">{message.content}</p>
                                    </div>
                                </div>
                            </div>
                        ))}
                        {isLoading && (
                            <div className="flex gap-3">
                                <div className="w-8 h-8 rounded-lg bg-[#78a530] text-white flex items-center justify-center">
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
                </div>

                {/* Input */}
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
                            placeholder="Discuss task breakdown..."
                            rows={1}
                            className="flex-1 resize-none border border-gray-200 rounded-lg px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#78a530]"
                            disabled={isLoading}
                        />
                        <button
                            onClick={sendMessage}
                            disabled={isLoading || !inputValue.trim()}
                            className="p-2 bg-[#78a530] text-white rounded-lg hover:bg-[#6b9429] transition disabled:opacity-50"
                        >
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                            </svg>
                        </button>
                        <button
                            onClick={handleFinalize}
                            disabled={isFinalizing || messages.length < 3}
                            className="px-4 py-2 bg-[#78a530] text-white rounded-lg text-sm font-medium hover:bg-[#6b9429] transition disabled:opacity-50"
                        >
                            {isFinalizing ? "Finalizing..." : "Finalize Tasks"}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    )
}
