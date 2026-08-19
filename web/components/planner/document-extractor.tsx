"use client"

import { useState, useRef, useEffect } from "react"
import { X, Upload, FileText, Trash2, Sparkles, MessageSquare, Plus } from "lucide-react"
import { useSession } from "next-auth/react"
import { ChatInterface } from "./chat-interface"
import { DraftPanel } from "./draft-panel"

interface UploadedDocument {
    file_id: string
    filename: string
    file_type: string
    upload_date?: string
    extracted?: boolean
}

type ExtractedContent = Record<string, string[]>
type OutOfScopeContent = Record<string, string[]>

type Stage = string

interface DocumentExtractorProps {
    projectId: string
    projectName?: string
    projectType?: string
    activeStage: Stage
    stages: Record<string, string>
    stageOrder: string[]
    stageLabels: Record<string, string>
    onExtracted: () => void
    onFinalize: (content: string[], outOfScope?: string[]) => void
    onClose: () => void
    onContinueToNext: (stage: string) => void
    onViewModeChange?: (mode: "upload" | "extracted" | "brainstorm") => void
    hasDraftContent?: boolean
    stageItems?: string[]
    stageOutOfScopeItems?: string[]
    uploadedDocs: UploadedDocument[]
    onDocumentsChanged?: () => Promise<void> | void
    isDarkMode?: boolean
}

export function DocumentExtractor({
    projectId,
    projectName = "",
    projectType = "Software / Product Development",
    activeStage,
    stages,
    stageOrder,
    stageLabels,
    onExtracted,
    onFinalize,
    onClose,
    onContinueToNext,
    onViewModeChange,
    hasDraftContent = false,
    stageItems = [],
    stageOutOfScopeItems = [],
    uploadedDocs: uploadedDocsProp,
    onDocumentsChanged,
    isDarkMode = false
}: DocumentExtractorProps) {
    const dm = isDarkMode
    const { data: session } = useSession()
    const [uploadedDocs, setUploadedDocs] = useState<UploadedDocument[]>([])
    const [showDocsPopover, setShowDocsPopover] = useState(false)
    const docsPopoverRef = useRef<HTMLDivElement | null>(null)
    const [extractedContent, setExtractedContent] = useState<ExtractedContent | null>(null)
    const [extractedOutOfScope, setExtractedOutOfScope] = useState<OutOfScopeContent>({})
    const [isUploading, setIsUploading] = useState(false)
    const [isExtracting, setIsExtracting] = useState(false)
    const [isLoadingData, setIsLoadingData] = useState(true)
    const [viewMode, setViewMode] = useState<"upload" | "extracted" | "brainstorm">("upload")
    const [showAddModal, setShowAddModal] = useState(false)
    const [newPointText, setNewPointText] = useState("")
    const [inlineEditIndex, setInlineEditIndex] = useState<number | null>(null)
    const [inlineEditValue, setInlineEditValue] = useState("")
    const [inlineEditList, setInlineEditList] = useState<"main" | "out_of_scope">("main")
    const [showToast, setShowToast] = useState(false)
    const [toastMessage, setToastMessage] = useState("")
    const fileInputRef = useRef<HTMLInputElement>(null)

    const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
    const backendToken = String(session?.user?.backendToken || "").trim()
    const backendFetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
        const headers = new Headers(init.headers || {})
        if (backendToken && !headers.has("Authorization")) {
            headers.set("Authorization", `Bearer ${backendToken}`)
        }
        return window.fetch(input, { ...init, headers })
    }
    const getStageLabel = (stage: string) => stageLabels[stage] || stage

    // Auto-hide toast after 3 seconds
    useEffect(() => {
        if (showToast) {
            const timer = setTimeout(() => {
                setShowToast(false)
            }, 3000)
            return () => clearTimeout(timer)
        }
    }, [showToast])

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

    const getNextStage = (): string => {
        const currentIndex = stageOrder.indexOf(activeStage)
        if (currentIndex >= 0 && currentIndex < stageOrder.length - 1) {
            return stageOrder[currentIndex + 1]
        }
        return activeStage
    }

    // Check if a stage is finalized (check both parent stages prop and local state)
    const isStageFinalized = (stage: string): boolean => {
        return stages[stage] === "finalized"
    }

    // Check if next stage is finalized
    const isNextStageFinalized = (): boolean => {
        const nextStage = getNextStage()
        if (nextStage === activeStage) return true // No next stage
        return isStageFinalized(nextStage)
    }

    // Show continue button if current is finalized and next is not finalized
    const shouldShowContinueButton = (): boolean => {
        const currentFinalized = isStageFinalized(activeStage)
        const isLastStage = activeStage === stageOrder[stageOrder.length - 1]
        const nextFinalized = isNextStageFinalized()

        return currentFinalized && !isLastStage && !nextFinalized
    }

    useEffect(() => {
        onViewModeChange?.(viewMode)
    }, [viewMode, onViewModeChange])

    useEffect(() => {
        if (hasDraftContent && viewMode === "upload") {
            setViewMode("extracted")
        }
    }, [hasDraftContent, viewMode])

    useEffect(() => {
        setUploadedDocs(Array.isArray(uploadedDocsProp) ? uploadedDocsProp : [])
    }, [uploadedDocsProp])

    useEffect(() => {
        const stageKey = activeStage
        const normalizedItems = Array.isArray(stageItems) ? stageItems : []
        const normalizedOut = Array.isArray(stageOutOfScopeItems) ? stageOutOfScopeItems : []

        setExtractedContent(prev => ({
            ...(prev || {}),
            [stageKey]: normalizedItems
        }))
        if (stageKey === "scope") {
            setExtractedOutOfScope(prev => ({ ...prev, scope: normalizedOut }))
        }
        setIsLoadingData(false)
    }, [activeStage, stageItems, stageOutOfScopeItems])

    const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const files = event.target.files
        if (!files || files.length === 0) return

        setIsUploading(true)

        try {
            const formData = new FormData()
            Array.from(files).forEach((file) => {
                formData.append("files", file)
            })

            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/upload-documents`, {
                method: "POST",
                body: formData
            })

            if (response.ok) {
                await onDocumentsChanged?.()
            } else {
                alert("Failed to upload documents")
            }
        } catch (error) {
            console.error("Error uploading documents:", error)
            alert("Error uploading documents")
        } finally {
            setIsUploading(false)
            if (fileInputRef.current) {
                fileInputRef.current.value = ""
            }
        }
    }

    const handleExtract = async () => {
        if (uploadedDocs.length === 0) {
            alert("Please upload documents first")
            return
        }

        setIsExtracting(true)

        try {
            const response = await backendFetch(`${backendUrl}/api/agent/document-extract`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: projectId,
                    project_type: projectType,
                    project_name: projectName,
                    file_ids: uploadedDocs.map(doc => doc.file_id)
                })
            })

            if (response.ok) {
                const data = await response.json()
                setExtractedContent(data.extracted_content)
                setExtractedOutOfScope(data.out_of_scope || {})
                setViewMode("extracted")
                onExtracted()
            } else {
                alert("Failed to extract content")
            }
        } catch (error) {
            console.error("Error extracting content:", error)
            alert("Error extracting content")
        } finally {
            setIsExtracting(false)
        }
    }

    const persistDraft = async (items: string[], outOfScope?: string[]) => {
        try {
            await backendFetch(`${backendUrl}/api/planner/${projectId}/draft/${activeStage}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ items, out_of_scope: outOfScope || [] })
            })
        } catch {
            // ignore
        }
        onExtracted()
    }

    const handleAddPoint = async () => {
        if (!newPointText.trim()) return
        const stageKey = activeStage
        const updatedContent = {
            ...(extractedContent || {}),
            [stageKey]: [...(extractedContent?.[stageKey] || []), newPointText.trim()]
        }
        setExtractedContent(updatedContent)
        setNewPointText("")
        setShowAddModal(false)
        const outOfScope = stageKey === "scope" ? (extractedOutOfScope.scope || []) : undefined
        await persistDraft(updatedContent[stageKey] || [], outOfScope)
    }

    // Save finalized content to backend
    const handleFinalizeAndSave = async () => {
        const stageKey = activeStage
        if (!extractedContent || !(extractedContent[stageKey] || []).length) return

        try {
            // Call parent's onFinalize which should update backend
            const outOfScope = stageKey === "scope" ? (extractedOutOfScope.scope || []) : undefined
            onFinalize(extractedContent[stageKey] || [], outOfScope)

            // Show success toast
            setToastMessage(`${stageLabels[activeStage] || activeStage} finalized successfully!`)
            setShowToast(true)
        } catch (error) {
            console.error("Error finalizing content:", error)
            alert("Failed to finalize. Please try again.")
            // Revert local state on error
        }
    }

    const handleDeleteDocument = async (fileId: string) => {
        try {
            const response = await backendFetch(`${backendUrl}/api/planner/${projectId}/documents/${fileId}`, {
                method: "DELETE"
            })

            if (response.ok) {
                await onDocumentsChanged?.()
            }
        } catch (error) {
            console.error("Error deleting document:", error)
        }
    }

    // Show loading spinner while checking for existing data
    if (isLoadingData && viewMode === "upload") {
        return (
            <div className="w-full h-full flex items-center justify-center">
                <div className="text-center">
                    <img
                        src="/logos/loading.gif"
                        alt="Loading..."
                        className="w-16 h-16 mx-auto mb-4"
                    />
                    <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-600"}`}>Loading charter data...</p>
                </div>
            </div>
        )
    }

    // Upload View - Only upload interface (NO TABS)
    if (viewMode === "upload") {
        return (
            <div className="w-full h-full">
                <div className="flex flex-col max-w-2xl mx-auto">
                    {/* Header */}
                    <div className="flex items-center justify-between mb-6">
                        <div>
                            <h2 className={`text-2xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>Extract from Documents</h2>
                            <p className={`text-sm mt-1 ${dm ? "text-gray-300" : "text-gray-600"}`}>
                                Upload project documents and we'll extract {getStageLabel(activeStage).toLowerCase()} automatically
                            </p>
                        </div>
                        <button
                            onClick={() => {
                                setViewMode("upload")
                                onClose()
                            }}
                            className={`p-2 rounded-lg transition ${dm ? "hover:bg-white/[0.08] text-gray-400" : "hover:bg-gray-100 text-gray-500"}`}
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>

                    {/* Upload Area */}
                    <div className="mb-6">
                        <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            accept=".docx,.doc,.pdf,.txt,.md,.markdown"
                            onChange={handleFileSelect}
                            className="hidden"
                        />
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            disabled={isUploading}
                            className={`w-full py-7 px-6 border-2 border-dashed rounded-xl hover:border-[#78a530] transition group ${dm ? "border-white/[0.15] hover:bg-white/[0.04]" : "border-gray-300 hover:bg-gray-50"}`}
                        >
                            <Upload className={`w-9 h-9 group-hover:text-[#78a530] mx-auto mb-3 ${dm ? "text-gray-400" : "text-gray-400"}`} />
                            <p className={`text-sm font-semibold group-hover:text-[#78a530] ${dm ? "text-gray-200" : "text-gray-700"}`}>
                                {isUploading ? "Uploading..." : "Click to upload documents"}
                            </p>
                            <p className={`text-xs mt-1.5 ${dm ? "text-gray-400" : "text-gray-500"}`}>
                                Supports Word, PDF, Markdown, TXT
                            </p>
                        </button>
                    </div>

                    {/* Uploaded Documents List */}
                    <div className={`flex-1 overflow-y-auto border rounded-xl p-4 mb-4 ${dm ? "border-white/[0.10] bg-white/[0.02]" : "border-gray-200"}`}>
                        <h3 className={`text-sm font-semibold mb-3 ${dm ? "text-gray-200" : "text-gray-700"}`}>Uploaded Documents</h3>
                        {uploadedDocs.length === 0 ? (
                            <p className={`text-sm text-center py-8 ${dm ? "text-gray-400" : "text-gray-500"}`}>
                                No documents uploaded yet
                            </p>
                        ) : (
                            <div className="space-y-2">
                                {uploadedDocs.map((doc) => (
                                    <div
                                        key={doc.file_id}
                                        className={`flex items-center gap-3 p-3 rounded-lg group ${dm ? "bg-white/[0.05]" : "bg-gray-50"}`}
                                    >
                                        <FileText className="w-5 h-5 text-blue-400 flex-shrink-0" />
                                        <div className="flex-1 min-w-0">
                                            <p className={`text-sm font-medium truncate ${dm ? "text-gray-100" : "text-gray-900"}`}>
                                                {doc.filename}
                                            </p>
                                            <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>
                                                {doc.file_type.toUpperCase()}
                                            </p>
                                        </div>
                                        <button
                                            onClick={() => handleDeleteDocument(doc.file_id)}
                                            className={`p-1.5 rounded transition opacity-0 group-hover:opacity-100 ${dm ? "hover:bg-red-900/30" : "hover:bg-red-100"}`}
                                        >
                                            <Trash2 className="w-4 h-4 text-red-400" />
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Extract Button */}
                    <button
                        onClick={handleExtract}
                        disabled={uploadedDocs.length === 0 || isExtracting}
                        className="w-full py-3 bg-[#78a530] text-white rounded-xl font-semibold hover:bg-[#6b9429] transition disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {isExtracting ? "Extracting..." : "Extract Content"}
                    </button>
                </div>
            </div>
        )
    }

    // Brainstorm View - Split screen (Chat on left, Extracted list on right)
    if (viewMode === "brainstorm") {
        const stageKey = activeStage
        const isFinalized = isStageFinalized(activeStage)

        return (
            <div className="w-full h-full">
                <div className="w-full flex gap-6 mt-6" style={{ height: "calc(100vh - 220px)" }}>
                    {/* Left Panel - Chat Interface */}
                    <div className="flex-1 relative">
                        <ChatInterface
                            projectId={projectId}
                            stage={activeStage}
                            mode="document"
                            projectType={projectType}
                            projectName={projectName}
                            stageLabel={getStageLabel(activeStage)}
                            isDarkMode={isDarkMode}
                            onFinalize={(content, outOfScope) => {
                                if (extractedContent) {
                                    const updatedContent = {
                                        ...extractedContent,
                                        [stageKey]: content
                                    }
                                    setExtractedContent(updatedContent)
                                }
                                if (stageKey === "scope" && outOfScope) {
                                    setExtractedOutOfScope(prev => ({ ...prev, scope: outOfScope }))
                                }
                                // Update parent (this should save to backend)
                                onFinalize(content, outOfScope)
                                setViewMode("extracted")
                            }}
                            onClose={() => setViewMode("extracted")}
                        />
                    </div>

                    {/* Right Panel - Current Extracted List */}
                    <div className="w-96 flex-shrink-0 flex flex-col">
                        <div className={`flex-1 rounded-xl p-4 overflow-y-auto mb-4 ${dm ? "bg-white/[0.04]" : "bg-gray-50"}`}>
                            <DraftPanel
                                title={getStageLabel(activeStage)}
                                items={extractedContent?.[stageKey] || []}
                                secondaryTitle={activeStage === "scope" ? "Out of Scope" : undefined}
                                secondaryItems={activeStage === "scope" ? (extractedOutOfScope.scope || []) : undefined}
                                isDraft={!isFinalized}
                                isGoal={activeStage === "goal"}
                                onFinalize={isFinalized ? undefined : handleFinalizeAndSave}
                                showFinalize={!isFinalized}
                            />
                        </div>

                        {/* Uploaded Documents */}
                        <div className={`w-full rounded-xl p-4 border-2 ${dm ? "bg-white/[0.04] border-white/[0.10]" : "bg-white border-gray-200"}`} style={{ maxHeight: "200px", overflow: "auto" }}>
                            <h4 className={`text-xs font-semibold mb-2 ${dm ? "text-gray-300" : "text-gray-700"}`}>Source Documents</h4>
                            <div className="space-y-1">
                                {uploadedDocs.map((doc) => (
                                    <div key={doc.file_id} className={`flex items-center gap-2 text-xs ${dm ? "text-gray-400" : "text-gray-600"}`}>
                                        <FileText className="w-3 h-3 text-blue-500" />
                                        <span className="truncate">{doc.filename}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        )
    }

    // Extracted View - Shows extracted content with document list on right
    const stageKey = activeStage
    const isFinalized = isStageFinalized(activeStage)
    const contentToShow = extractedContent?.[stageKey] || []
    const scopeOutOfScope = activeStage === "scope" ? (extractedOutOfScope.scope || []) : []

    const handleInlineEdit = (index: number, value: string, list: "main" | "out_of_scope" = "main") => {
        setInlineEditIndex(index)
        setInlineEditValue(value)
        setInlineEditList(list)
    }

    const handleInlineDelete = async (index: number, list: "main" | "out_of_scope" = "main") => {
        if (list === "out_of_scope") {
            const updatedOut = (scopeOutOfScope || []).filter((_, idx) => idx !== index)
            setExtractedOutOfScope(prev => ({ ...prev, scope: updatedOut }))
            await persistDraft(contentToShow, updatedOut)
            return
        }
        const updatedItems = (contentToShow || []).filter((_, idx) => idx !== index)
        setExtractedContent(prev => ({ ...(prev || {}), [stageKey]: updatedItems }))
        await persistDraft(updatedItems, scopeOutOfScope)
    }

    const handleInlineSave = async () => {
        if (inlineEditIndex === null || !inlineEditValue.trim()) return
        if (inlineEditList === "out_of_scope") {
            const updatedOut = (scopeOutOfScope || []).map((item, idx) =>
                idx === inlineEditIndex ? inlineEditValue.trim() : item
            )
            setExtractedOutOfScope(prev => ({ ...prev, scope: updatedOut }))
            await persistDraft(contentToShow, updatedOut)
        } else {
            const updatedItems = (contentToShow || []).map((item, idx) =>
                idx === inlineEditIndex ? inlineEditValue.trim() : item
            )
            setExtractedContent(prev => ({ ...(prev || {}), [stageKey]: updatedItems }))
            await persistDraft(updatedItems, scopeOutOfScope)
        }
        setInlineEditIndex(null)
        setInlineEditValue("")
    }

    const renderInlineEditor = () => (
        <>
            <textarea
                value={inlineEditValue}
                onChange={(e) => setInlineEditValue(e.target.value)}
                className={`w-full p-2 text-sm rounded-lg focus:ring-2 focus:ring-[#78a530] focus:border-transparent resize-none ${dm ? "border border-white/[0.15] bg-white/[0.06] text-gray-100 placeholder-gray-500" : "border border-gray-200 bg-white text-gray-800"}`}
                rows={2}
                autoFocus
            />
            <div className="flex gap-2 justify-end mt-2">
                <button
                    onClick={() => {
                        setInlineEditIndex(null)
                        setInlineEditValue("")
                    }}
                    className={`px-3 py-1 text-xs ${dm ? "text-gray-400 hover:text-gray-200" : "text-gray-600 hover:text-gray-800"}`}
                >
                    Cancel
                </button>
                <button
                    onClick={handleInlineSave}
                    className="px-3 py-1 text-xs bg-[#78a530] text-white rounded-md hover:bg-[#6b9429]"
                >
                    Save
                </button>
            </div>
        </>
    )

    const renderCardList = () => {
        if (activeStage === "feature") {
            return (
                <>
                    {(contentToShow || []).map((feature, idx) => (
                        <div key={idx} className={`rounded-xl border p-4 shadow-sm hover:shadow-md transition group ${dm ? "bg-white/[0.05] border-white/[0.10]" : "bg-white border-gray-200"}`}>
                        <div className="flex items-start gap-4">
                            <div className={`w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 ${dm ? "bg-white/[0.10]" : "bg-gray-100"}`}>
                                <span className={`text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"}`}>{idx + 1}</span>
                            </div>
                            <div className="flex-1">
                                {inlineEditIndex === idx && inlineEditList === "main" ? (
                                    renderInlineEditor()
                                ) : (
                                    <p className={`text-base pt-2 ${dm ? "text-gray-100" : "text-gray-800"}`}>{feature}</p>
                                )}
                            </div>
                            {inlineEditIndex !== idx && (
                                <div className="flex items-center gap-2 pt-2">
                                    <button
                                        onClick={() => handleInlineEdit(idx, feature)}
                                        className="opacity-0 group-hover:opacity-100 transition text-blue-500 hover:text-blue-700"
                                        title="Edit"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                        </svg>
                                    </button>
                                    <button
                                        onClick={() => handleInlineDelete(idx)}
                                        className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700"
                                        title="Delete"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                    </button>
                                </div>
                            )}
                        </div>
                        </div>
                    ))}
                </>
            )
        }

        if (activeStage === "goal") {
            return (
                <>
                    {(contentToShow || []).map((item, idx) => (
                        <div key={idx} className={`rounded-xl border p-4 shadow-sm hover:shadow-md transition group ${dm ? "bg-white/[0.05] border-white/[0.10]" : "bg-white border-gray-200"}`}>
                        <div className="flex items-start gap-4">
                            <div className="flex-1">
                                {inlineEditIndex === idx && inlineEditList === "main" ? (
                                    renderInlineEditor()
                                ) : (
                                    <p className={`text-base ${dm ? "text-gray-100" : "text-gray-800"}`}>{item}</p>
                                )}
                            </div>
                            {inlineEditIndex !== idx && (
                                <div className="flex items-center gap-2 pt-1">
                                    <button
                                        onClick={() => handleInlineEdit(idx, item)}
                                        className="opacity-0 group-hover:opacity-100 transition text-blue-500 hover:text-blue-700"
                                        title="Edit"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                        </svg>
                                    </button>
                                    <button
                                        onClick={() => handleInlineDelete(idx)}
                                        className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700"
                                        title="Delete"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                    </button>
                                </div>
                            )}
                        </div>
                        </div>
                    ))}
                </>
            )
        }

        if (activeStage === "scope") {
            return (
                <div className="space-y-6">
                    <div>
                        <div className="flex items-center gap-2 mb-3">
                            <div className="w-7 h-7 bg-[#78a530]/10 rounded-full flex items-center justify-center">
                                <span className="text-[#78a530] text-xs font-bold">✓</span>
                            </div>
                            <h3 className={`text-sm font-semibold ${dm ? "text-gray-200" : "text-gray-700"}`}>In Scope</h3>
                        </div>
                        {contentToShow.length > 0 ? (
                            <div className="space-y-4">
                                {contentToShow.map((item, idx) => (
                                    <div key={`scope-${idx}`} className={`rounded-xl border p-4 shadow-sm hover:shadow-md transition group ${dm ? "bg-white/[0.05] border-white/[0.10]" : "bg-white border-gray-200"}`}>
                                        <div className="flex items-start gap-4">
                                            <div className={`w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 ${dm ? "bg-white/[0.10]" : "bg-gray-100"}`}>
                                                <span className={`text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"}`}>{idx + 1}</span>
                                            </div>
                                            <div className="flex-1">
                                                {inlineEditIndex === idx && inlineEditList === "main" ? (
                                                    renderInlineEditor()
                                                ) : (
                                                    <p className={`text-base pt-2 ${dm ? "text-gray-100" : "text-gray-800"}`}>{item}</p>
                                                )}
                                            </div>
                                            {inlineEditIndex !== idx && (
                                                <div className="flex items-center gap-2 pt-2">
                                                    <button
                                                        onClick={() => handleInlineEdit(idx, item)}
                                                        className="opacity-0 group-hover:opacity-100 transition text-blue-500 hover:text-blue-700"
                                                        title="Edit"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                                        </svg>
                                                    </button>
                                                    <button
                                                        onClick={() => handleInlineDelete(idx)}
                                                        className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700"
                                                        title="Delete"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                        </svg>
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>No in-scope items yet.</p>
                        )}
                    </div>

                    <div>
                        <div className="flex items-center gap-2 mb-3">
                            <div className={`w-7 h-7 rounded-full flex items-center justify-center ${dm ? "bg-rose-900/40" : "bg-rose-100"}`}>
                                <span className="text-rose-500 text-xs font-bold">!</span>
                            </div>
                            <h3 className={`text-sm font-semibold ${dm ? "text-rose-400" : "text-rose-600"}`}>Out of Scope</h3>
                        </div>
                        {scopeOutOfScope.length > 0 ? (
                            <div className="space-y-4">
                                {scopeOutOfScope.map((item, idx) => (
                                    <div key={`out-scope-${idx}`} className={`rounded-xl border p-4 shadow-sm hover:shadow-md transition group ${dm ? "bg-white/[0.05] border-white/[0.10]" : "bg-white border-gray-200"}`}>
                                        <div className="flex items-start gap-4">
                                            <div className={`w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 ${dm ? "bg-rose-900/40" : "bg-rose-50"}`}>
                                                <span className="text-rose-500 text-sm font-bold">{idx + 1}</span>
                                            </div>
                                            <div className="flex-1">
                                                {inlineEditIndex === idx && inlineEditList === "out_of_scope" ? (
                                                    renderInlineEditor()
                                                ) : (
                                                    <p className={`text-base pt-2 ${dm ? "text-gray-100" : "text-gray-800"}`}>{item}</p>
                                                )}
                                            </div>
                                            {inlineEditIndex !== idx && (
                                                <div className="flex items-center gap-2 pt-2">
                                                    <button
                                                        onClick={() => handleInlineEdit(idx, item, "out_of_scope")}
                                                        className="opacity-0 group-hover:opacity-100 transition text-blue-500 hover:text-blue-700"
                                                        title="Edit"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                                        </svg>
                                                    </button>
                                                    <button
                                                        onClick={() => handleInlineDelete(idx, "out_of_scope")}
                                                        className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700"
                                                        title="Delete"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                        </svg>
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>No out-of-scope items yet.</p>
                        )}
                    </div>
                </div>
            )
        }

        return (
            <>
                {(contentToShow || []).map((item, idx) => (
                    <div key={idx} className={`rounded-xl border p-4 shadow-sm hover:shadow-md transition group ${dm ? "bg-white/[0.05] border-white/[0.10]" : "bg-white border-gray-200"}`}>
                        <div className="flex items-start gap-4">
                            <div className={`w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 ${dm ? "bg-white/[0.10]" : "bg-gray-100"}`}>
                                <span className={`text-sm font-bold ${dm ? "text-gray-200" : "text-gray-700"}`}>{idx + 1}</span>
                            </div>
                            <div className="flex-1">
                                {inlineEditIndex === idx && inlineEditList === "main" ? (
                                    renderInlineEditor()
                                ) : (
                                    <p className={`text-base pt-2 ${dm ? "text-gray-100" : "text-gray-800"}`}>{item}</p>
                                )}
                            </div>
                            {inlineEditIndex !== idx && (
                                <div className="flex items-center gap-2 pt-2">
                                    <button
                                        onClick={() => handleInlineEdit(idx, item)}
                                        className="opacity-0 group-hover:opacity-100 transition text-blue-500 hover:text-blue-700"
                                        title="Edit"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                        </svg>
                                    </button>
                                    <button
                                        onClick={() => handleInlineDelete(idx)}
                                        className="opacity-0 group-hover:opacity-100 transition text-red-500 hover:text-red-700"
                                        title="Delete"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                ))}
            </>
        )
    }

    return (
        <div className="w-full h-full">
            <div className="w-full flex gap-6 mt-1">
                {/* Left Panel - Extracted Content (60%) */}
                <div className="flex-[3]">
                    <div className="flex items-center justify-between gap-2 mb-2">
                        <div className="flex items-center gap-3">
                            <div className={`w-8 h-8 rounded-full flex items-center justify-center ${isFinalized ? "bg-[#78a530]" : "bg-gray-400"}`}>
                                <span className="text-white text-sm font-bold">{isFinalized ? "✓" : stageOrder.indexOf(activeStage) + 1}</span>
                            </div>
                            <h2 className={`text-xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>{getStageLabel(activeStage)}</h2>
                        </div>
                        {uploadedDocs.length > 0 && (
                            <div className="relative" ref={docsPopoverRef}>
                                <button
                                    onClick={() => setShowDocsPopover(prev => !prev)}
                                    className={`px-3 py-1.5 text-sm rounded-lg transition hover:border-[#78a530] hover:text-[#78a530] ${dm ? "border border-white/[0.12] text-gray-300" : "border border-gray-200 text-gray-600"}`}
                                >
                                    Source Documents ({uploadedDocs.length})
                                </button>
                                {showDocsPopover && (
                                    <div className={`absolute right-0 mt-2 w-80 rounded-xl shadow-lg p-4 z-20 border ${dm ? "bg-[#1e2638] border-white/[0.10]" : "bg-white border-gray-200"}`}>
                                        <h4 className={`text-sm font-semibold mb-3 ${dm ? "text-gray-200" : "text-gray-800"}`}>Source Documents</h4>
                                        <div className="space-y-2 max-h-64 overflow-auto">
                                            {uploadedDocs.map(doc => (
                                                <div key={doc.file_id} className={`flex items-center gap-3 p-2 rounded-lg ${dm ? "bg-white/[0.06]" : "bg-gray-50"}`}>
                                                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center border ${dm ? "bg-white/[0.08] border-white/[0.10]" : "bg-white border-gray-200"}`}>
                                                        <span className={`text-xs ${dm ? "text-gray-300" : "text-gray-600"}`}>{doc.file_type.toUpperCase()}</span>
                                                    </div>
                                                    <div className="min-w-0">
                                                        <p className={`text-sm truncate ${dm ? "text-gray-100" : "text-gray-800"}`}>{doc.filename}</p>
                                                        <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>{doc.file_type.toUpperCase()}</p>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>

                    <div className="space-y-4 mb-6">
                        {renderCardList()}
                    </div>

                    <div className="flex justify-center gap-4 mt-6">
                        <button
                            onClick={() => setViewMode("brainstorm")}
                            className={`px-4 py-2 text-sm rounded-lg transition flex items-center gap-2 hover:border-[#78a530] hover:text-[#78a530] ${dm ? "border border-white/[0.12] text-gray-300" : "border border-gray-200 text-gray-600"}`}
                        >
                            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
                            </svg>
                            Brainstorm More
                        </button>

                        <button
                            onClick={() => {
                                setShowAddModal(true)
                            }}
                            className={`px-4 py-2 text-sm rounded-lg transition hover:border-[#78a530] hover:text-[#78a530] ${dm ? "border border-white/[0.12] text-gray-300" : "border border-gray-200 text-gray-600"}`}
                        >
                            Add new {getStageLabel(activeStage).toLowerCase()}
                        </button>

                        {!isFinalized && (
                            <button
                                onClick={handleFinalizeAndSave}
                                className="px-4 py-2 text-sm bg-[#78a530] text-white rounded-lg font-medium hover:bg-[#6b9429] transition"
                            >
                                Finalize {getStageLabel(activeStage)}
                            </button>
                        )}

                        {shouldShowContinueButton() && (
                            <button
                                onClick={async () => {
                                    if (extractedContent) {
                                        const nextStage = getNextStage()
                                        if (nextStage !== activeStage) {
                                            setViewMode("extracted")
                                            onContinueToNext(nextStage)
                                        }
                                    }
                                }}
                                className="px-4 py-2 text-sm bg-[#78a530] text-white rounded-lg font-medium hover:bg-[#6b9429] transition"
                            >
                                Continue to {getStageLabel(getNextStage())} →
                            </button>
                        )}
                    </div>
                </div>

                {/* Right Panel - removed (documents now in popover) */}
            </div>

            {/* Add Point Modal */}
            {showAddModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className={`rounded-2xl p-6 w-[500px] max-w-[90%] shadow-xl ${dm ? "bg-[#1e2638] border border-white/[0.12]" : "bg-white"}`}>
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-10 h-10 bg-[#78a530] rounded-full flex items-center justify-center">
                                <Plus className="w-5 h-5 text-white" />
                            </div>
                            <h3 className={`text-xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>
                                Add New {getStageLabel(activeStage)}
                            </h3>
                        </div>

                        <textarea
                            value={newPointText}
                            onChange={(e) => setNewPointText(e.target.value)}
                            placeholder={`Enter new ${getStageLabel(activeStage).toLowerCase()}...`}
                            rows={3}
                            className={`w-full rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent resize-none ${dm ? "border border-white/[0.15] bg-white/[0.06] text-gray-100 placeholder-gray-500" : "border border-gray-200 text-gray-800 placeholder-gray-400"}`}
                        />

                        <div className="flex gap-3 mt-4">
                            <button
                                onClick={() => {
                                    setShowAddModal(false)
                                    setNewPointText("")
                                }}
                                className={`flex-1 px-4 py-2 rounded-lg transition ${dm ? "border border-white/[0.12] text-gray-300 hover:bg-white/[0.06]" : "border border-gray-300 text-gray-700 hover:bg-gray-50"}`}
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleAddPoint}
                                disabled={!newPointText.trim()}
                                className="flex-1 px-4 py-2 bg-[#78a530] text-white rounded-lg hover:bg-[#6b9429] transition disabled:opacity-50"
                            >
                                Add
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Toast Notification */}
            {showToast && (
                <div className="fixed bottom-4 right-4 z-50 animate-fade-in">
                    <div className="bg-[#78a530] text-white px-6 py-3 rounded-lg shadow-lg flex items-center gap-3">
                        <div className="w-5 h-5 bg-white rounded-full flex items-center justify-center">
                            <span className="text-[#78a530] text-sm font-bold">✓</span>
                        </div>
                        <p className="text-sm font-medium">{toastMessage}</p>
                    </div>
                </div>
            )}
        </div>
    )
}
