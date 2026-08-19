"use client"

import { useState, useRef, type ReactNode } from "react"
import { FloatingTabMenu, FloatingTabButton } from "@/components/ui/floating-tab-menu"

interface TopicGroup {
    id: string
    name: string
    display_order?: number
}

interface TopicItem {
    topic_id: string
    stage_id: string
    name: string
    group_id: string
    status: string
    display_order?: number
}

interface TopicNavProps {
    groups: TopicGroup[]
    topics: TopicItem[]
    selectedGroupId: string
    activeStage: string
    onSelectGroup: (groupId: string) => void
    onSelectStage: (stageId: string) => void
    onAddTopic: () => void
    rightActions?: ReactNode
    isDarkMode?: boolean
}

const statusStyles: Record<string, string> = {
    finalized: "bg-[#78a530] text-white",
    in_progress: "bg-amber-400 text-white",
    not_started: "bg-gray-300 text-gray-700",
    pending: "bg-gray-300 text-gray-700",
    draft: "bg-gray-300 text-gray-700"
}

const statusLabel: Record<string, string> = {
    finalized: "Finalized",
    in_progress: "In Progress",
    not_started: "Not Started",
    pending: "Not Started",
    draft: "In Progress"
}

export function TopicNav({
    groups,
    topics,
    selectedGroupId,
    activeStage,
    onSelectGroup,
    onSelectStage,
    onAddTopic,
    rightActions,
    isDarkMode = false
}: TopicNavProps) {
    const useGroupedNav = false
    const sortedGroups = [...groups]
        .sort((a, b) => (a.display_order ?? 99) - (b.display_order ?? 99))
        .filter(group => topics.some(topic => topic.group_id === group.id))
    const activeGroupId = sortedGroups.find(group => group.id === selectedGroupId)?.id || sortedGroups[0]?.id || ""
    const [openGroupId, setOpenGroupId] = useState<string | null>(null)
    const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
    const closeDelayMs = 120

    const clearCloseTimer = () => {
        if (closeTimerRef.current) {
            clearTimeout(closeTimerRef.current)
            closeTimerRef.current = null
        }
    }

    const scheduleClose = () => {
        clearCloseTimer()
        closeTimerRef.current = setTimeout(() => {
            setOpenGroupId(null)
        }, closeDelayMs)
    }

    return (
        <FloatingTabMenu
            isDarkMode={isDarkMode}
            containerClassName="mb-1 flex items-center justify-between gap-4"
            rightActions={
                <div className="flex items-center gap-2">
                    {rightActions}
                    <button
                        onClick={onAddTopic}
                        className={`px-4 py-2 rounded-full text-sm font-semibold border transition ${isDarkMode ? "border-white/20 text-gray-300 hover:border-[#78a530] hover:text-[#78a530]" : "border-gray-200 text-gray-700 hover:border-[#78a530] hover:text-[#78a530]"}`}
                    >
                        + Add Topic
                    </button>
                </div>
            }
        >
            {useGroupedNav ? (
                <>
                    {/* Grouped hover menu (kept for easy toggle) */}
                    {sortedGroups.map(group => {
                        const isActive = group.id === activeGroupId
                        const isOpen = openGroupId === group.id
                        const groupTopics = topics.filter(topic => topic.group_id === group.id)
                        return (
                            <div
                                key={group.id}
                                className="relative"
                                onMouseEnter={() => {
                                    clearCloseTimer()
                                    setOpenGroupId(group.id)
                                }}
                                onMouseLeave={scheduleClose}
                            >
                                <FloatingTabButton
                                    isDarkMode={isDarkMode}
                                    active={isActive}
                                    onClick={() => {
                                        clearCloseTimer()
                                        onSelectGroup(group.id)
                                        setOpenGroupId(prev => (prev === group.id ? null : group.id))
                                    }}
                                    className="text-sm font-medium"
                                >
                                    {group.name}
                                </FloatingTabButton>

                                {isOpen && groupTopics.length > 0 && (
                                    <div
                                        className="absolute left-0 top-full mt-1 z-20 w-72 rounded-xl border border-gray-200 bg-white shadow-lg p-3"
                                        onMouseEnter={clearCloseTimer}
                                        onMouseLeave={scheduleClose}
                                    >
                                        <div className="flex flex-col gap-2">
                                            {groupTopics.map(topic => {
                                                const isTopicActive = topic.stage_id === activeStage
                                                const badgeClass = statusStyles[topic.status] || statusStyles.not_started
                                                const badgeText = statusLabel[topic.status] || statusLabel.not_started
                                                return (
                                                    <button
                                                        key={topic.topic_id}
                                                        onClick={() => onSelectStage(topic.stage_id)}
                                                        className={`flex items-center justify-between gap-2 px-3 py-2 rounded-lg border transition text-left ${isTopicActive
                                                                ? "border-[#78a530] bg-[#78a530]/10"
                                                                : "border-gray-200 bg-white hover:border-[#78a530]"
                                                            }`}
                                                    >
                                                        <span className="text-sm font-semibold text-gray-900">{topic.name}</span>
                                                        <span className={`text-xs px-2 py-0.5 rounded-full ${badgeClass}`}>
                                                            {badgeText}
                                                        </span>
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )
                    })}
                </>
            ) : (
                <>
                    {/* Flat topic list (current) */}
                    {[...topics]
                        .sort((a, b) => (a.display_order ?? 99) - (b.display_order ?? 99))
                        .map(topic => (
                            <FloatingTabButton
                                key={topic.topic_id}
                                isDarkMode={isDarkMode}
                                active={topic.stage_id === activeStage}
                                onClick={() => onSelectStage(topic.stage_id)}
                                className="text-sm font-medium"
                            >
                                {topic.name}
                            </FloatingTabButton>
                        ))}
                </>
            )}
        </FloatingTabMenu>
    )
}
