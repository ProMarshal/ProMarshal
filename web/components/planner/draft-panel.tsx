"use client"

import { useState } from "react"

interface DraftPanelProps {
    title: string
    items: string[]
    secondaryTitle?: string
    secondaryItems?: string[]
    onEditSecondaryItem?: (index: number, newValue: string) => void
    onDeleteSecondaryItem?: (index: number) => void
    gaps?: string[]
    isDraft?: boolean
    isGoal?: boolean // Goal shows as single statement, not numbered list
    onAddItem?: () => void
    onEditItem?: (index: number, newValue: string) => void
    onDeleteItem?: (index: number) => void
    onFinalize?: () => void
    showFinalize?: boolean
    showHeader?: boolean
}

export function DraftPanel({
    title,
    items,
    secondaryTitle,
    secondaryItems = [],
    onEditSecondaryItem,
    onDeleteSecondaryItem,
    gaps = [],
    isDraft = false,
    isGoal = false,
    onAddItem,
    onEditItem,
    onDeleteItem,
    onFinalize,
    showFinalize = true,
    showHeader = true
}: DraftPanelProps) {
    const [editingIndex, setEditingIndex] = useState<number | null>(null)
    const [editValue, setEditValue] = useState("")
    const [deletingIndex, setDeletingIndex] = useState<number | null>(null)
    const [secondaryEditingIndex, setSecondaryEditingIndex] = useState<number | null>(null)
    const [secondaryEditValue, setSecondaryEditValue] = useState("")
    const [secondaryDeletingIndex, setSecondaryDeletingIndex] = useState<number | null>(null)
    const [newItemAnimation, setNewItemAnimation] = useState<number | null>(null)

    const handleStartEdit = (index: number, currentValue: string) => {
        setEditingIndex(index)
        setEditValue(currentValue)
    }

    const handleSaveEdit = () => {
        if (editingIndex !== null && onEditItem) {
            onEditItem(editingIndex, editValue)
        }
        setEditingIndex(null)
        setEditValue("")
    }

    const handleDelete = (index: number) => {
        setDeletingIndex(index)
        setTimeout(() => {
            if (onDeleteItem) {
                onDeleteItem(index)
            }
            setDeletingIndex(null)
        }, 250)
    }

    const handleStartSecondaryEdit = (index: number, currentValue: string) => {
        setSecondaryEditingIndex(index)
        setSecondaryEditValue(currentValue)
    }

    const handleSaveSecondaryEdit = () => {
        if (secondaryEditingIndex !== null && onEditSecondaryItem) {
            onEditSecondaryItem(secondaryEditingIndex, secondaryEditValue)
        }
        setSecondaryEditingIndex(null)
        setSecondaryEditValue("")
    }

    const handleSecondaryDelete = (index: number) => {
        setSecondaryDeletingIndex(index)
        setTimeout(() => {
            if (onDeleteSecondaryItem) {
                onDeleteSecondaryItem(index)
            }
            setSecondaryDeletingIndex(null)
        }, 250)
    }

    return (
        <div className="flex flex-col h-full panel-transition">
            {/* Header */}
            {showHeader && (
                <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-3">
                        <div className={`w-8 h-8 rounded-xl flex items-center justify-center ${isDraft
                            ? "bg-gradient-to-br from-gray-400 to-gray-500"
                            : "bg-gradient-to-br from-[#78a530] to-[#5d8024] success-glow"
                            }`}>
                            <span className="text-white text-sm">✓</span>
                        </div>
                        <div>
                            <h3 className="text-base font-bold text-gray-900">
                                {title}
                            </h3>
                            {isDraft && (
                                <span className="text-xs text-gray-500">Draft • {items.length} items</span>
                            )}
                        </div>
                    </div>
                    {onAddItem && (
                        <button
                            onClick={onAddItem}
                            className="p-2 text-[#78a530] hover:bg-[#78a530]/10 rounded-lg transition"
                            title="Add item"
                        >
                            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                            </svg>
                        </button>
                    )}
                </div>
            )}

            {/* Items List */}
            <div className="flex-1 overflow-y-auto space-y-3 pr-1">
                {items.length > 0 ? (
                    items.map((item, idx) => (
                        <div
                            key={idx}
                            className={`glass-card p-4 draft-item-hover group ${deletingIndex === idx ? "fade-shrink" : ""
                                } ${newItemAnimation === idx ? "slide-in-left" : ""}`}
                        >
                            {editingIndex === idx ? (
                                <div className="flex flex-col gap-2">
                                    <textarea
                                        value={editValue}
                                        onChange={(e) => setEditValue(e.target.value)}
                                        className="w-full p-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-[#78a530] focus:border-transparent resize-none"
                                        rows={2}
                                        autoFocus
                                    />
                                    <div className="flex gap-2 justify-end">
                                        <button
                                            onClick={() => setEditingIndex(null)}
                                            className="px-3 py-1 text-xs text-gray-600 hover:text-gray-800"
                                        >
                                            Cancel
                                        </button>
                                        <button
                                            onClick={handleSaveEdit}
                                            className="px-3 py-1 text-xs bg-[#78a530] text-white rounded-md hover:bg-[#6b9429]"
                                        >
                                            Save
                                        </button>
                                    </div>
                                </div>
                            ) : (
                                <div className="flex items-start gap-3">
                                    {/* Hide numbering for Goal (single statement) */}
                                    {!isGoal && (
                                        <div className="w-6 h-6 bg-gray-100 rounded-full flex items-center justify-center flex-shrink-0">
                                            <span className="text-gray-700 text-xs font-bold">{idx + 1}</span>
                                        </div>
                                    )}
                                    <p className="text-sm text-gray-800 flex-1">
                                        {item}
                                    </p>
                                    {/* Edit button */}
                                    <button
                                        onClick={() => handleStartEdit(idx, item)}
                                        className="opacity-0 group-hover:opacity-100 p-1 text-blue-500 hover:text-blue-700 transition"
                                        title="Edit"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                        </svg>
                                    </button>
                                    {/* Delete button */}
                                    <button
                                        onClick={() => handleDelete(idx)}
                                        className="opacity-0 group-hover:opacity-100 p-1 text-red-500 hover:text-red-700 transition"
                                        title="Delete"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                    </button>
                                </div>
                            )}
                        </div>
                    ))
                ) : (
                    <div className="text-center py-8 text-gray-500">
                        <div className="w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
                            <svg className="w-6 h-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                            </svg>
                        </div>
                        <p className="text-sm">No items yet</p>
                        <p className="text-xs text-gray-400">Chat with Marshal to add items</p>
                    </div>
                )}

                {secondaryTitle && (
                    <div className="mt-6 pt-4 border-t border-dashed border-gray-200">
                        <div className="flex items-center gap-2 mb-3">
                            <div className="w-6 h-6 bg-rose-100 rounded-full flex items-center justify-center">
                                <span className="text-rose-700 text-xs font-bold">!</span>
                            </div>
                            <span className="text-xs font-semibold text-rose-600">{secondaryTitle}</span>
                        </div>
                        {secondaryItems.length > 0 ? (
                            <div className="space-y-2">
                                {secondaryItems.map((item, idx) => (
                                    <div
                                        key={`secondary-${idx}`}
                                        className={`glass-card p-4 group ${secondaryDeletingIndex === idx ? "fade-shrink" : ""}`}
                                    >
                                        {secondaryEditingIndex === idx ? (
                                            <div className="flex flex-col gap-2">
                                                <textarea
                                                    value={secondaryEditValue}
                                                    onChange={(e) => setSecondaryEditValue(e.target.value)}
                                                    className="w-full p-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-[#78a530] focus:border-transparent resize-none"
                                                    rows={2}
                                                    autoFocus
                                                />
                                                <div className="flex gap-2 justify-end">
                                                    <button
                                                        onClick={() => setSecondaryEditingIndex(null)}
                                                        className="px-3 py-1 text-xs text-gray-600 hover:text-gray-800"
                                                    >
                                                        Cancel
                                                    </button>
                                                    <button
                                                        onClick={handleSaveSecondaryEdit}
                                                        className="px-3 py-1 text-xs bg-[#78a530] text-white rounded-md hover:bg-[#6b9429]"
                                                    >
                                                        Save
                                                    </button>
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="flex items-start gap-3">
                                                <div className="w-6 h-6 bg-rose-50 rounded-full flex items-center justify-center flex-shrink-0">
                                                    <span className="text-rose-700 text-xs font-bold">{idx + 1}</span>
                                                </div>
                                                <p className="text-sm text-gray-800 flex-1">
                                                    {item}
                                                </p>
                                                {onEditSecondaryItem && (
                                                    <button
                                                        onClick={() => handleStartSecondaryEdit(idx, item)}
                                                        className="opacity-0 group-hover:opacity-100 p-1 text-blue-500 hover:text-blue-700 transition"
                                                        title="Edit"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                                        </svg>
                                                    </button>
                                                )}
                                                {onDeleteSecondaryItem && (
                                                    <button
                                                        onClick={() => handleSecondaryDelete(idx)}
                                                        className="opacity-0 group-hover:opacity-100 p-1 text-red-500 hover:text-red-700 transition"
                                                        title="Delete"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                        </svg>
                                                    </button>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="text-xs text-gray-400">No items yet</p>
                        )}
                    </div>
                )}

                {/* Gap Indicators */}
                {gaps.length > 0 && (
                    <div className="mt-4 pt-4 border-t border-dashed border-amber-300">
                        <div className="flex items-center gap-2 mb-3">
                            <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                            </svg>
                            <span className="text-xs font-semibold text-amber-600">Missing items</span>
                        </div>
                        {gaps.map((gap, idx) => (
                            <div
                                key={`gap-${idx}`}
                                className="gap-indicator p-3 mb-2 flex items-center gap-2"
                            >
                                <span className="text-amber-500">⚠️</span>
                                <span className="text-sm text-amber-700">{gap}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Finalize Button */}
            {
                showFinalize && items.length > 0 && (
                    <button
                        onClick={onFinalize}
                        className="mt-4 w-full py-3 bg-gradient-to-r from-[#78a530] to-[#5d8024] text-white rounded-xl font-semibold hover:shadow-lg transition-all duration-200 success-glow"
                    >
                        ✓ Finalize {title}
                    </button>
                )
            }
        </div >
    )
}
