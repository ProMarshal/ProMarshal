'use client'

import { useEffect, useState } from 'react'
import { useSession } from "next-auth/react"

interface Participation {
    responded: string[]
    not_responded: string[]
    total: number
    rate: number
}

interface AttendanceEntry {
    member: string
    attended: boolean
}

interface MemberTask {
    title: string
    key?: string
    status?: string
}

interface MemberNotableComment {
    task_title?: string
    task_key?: string
    comment: string
    tag?: string
}

interface MemberUpdate {
    member: string
    response_time_minutes?: number | null
    closed_tasks: MemberTask[]
    in_progress_tasks: MemberTask[]
    backlog_pickups: MemberTask[]
    backlog_action?: string | null
    notable_comments: MemberNotableComment[]
    key_input?: string | null
    overall_update?: string | null
}

interface NonResponder {
    member: string
    reason?: string
}

interface CadenceSummaryData {
    date: string
    participation: Participation
    attendance?: AttendanceEntry[]
    overall_summary?: string
    pm_attention_points?: string[]
    member_updates: MemberUpdate[]
    non_responders: NonResponder[]
}

interface CadenceSummaryResponse {
    status: 'ready' | 'in_progress' | 'no_data'
    date: string
    summary: CadenceSummaryData | null
}

interface CadenceSummaryProps {
    projectId: string
    isDarkMode?: boolean
}

function getTodayLocalDateString(): string {
    const now = new Date()
    const year = now.getFullYear()
    const month = String(now.getMonth() + 1).padStart(2, '0')
    const day = String(now.getDate()).padStart(2, '0')
    return `${year}-${month}-${day}`
}

function shiftDate(dateStr: string, days: number): string {
    const [year, month, day] = dateStr.split('-').map(Number)
    const shifted = new Date(year, (month || 1) - 1, day || 1)
    shifted.setDate(shifted.getDate() + days)
    const y = shifted.getFullYear()
    const m = String(shifted.getMonth() + 1).padStart(2, '0')
    const d = String(shifted.getDate()).padStart(2, '0')
    return `${y}-${m}-${d}`
}

export default function CadenceSummary({ projectId, isDarkMode = false }: CadenceSummaryProps) {
    const dm = isDarkMode
    const { data: session } = useSession()
    const backendToken = String(session?.user?.backendToken || "").trim()
    const cardStyle = dm
        ? { background: 'linear-gradient(160deg, #080d1a 0%, #0d1225 50%, #080c18 100%)', boxShadow: '0 0 0 1px rgba(255,255,255,0.12), inset 0 1px 0 rgba(255,255,255,0.08), 0 8px 40px rgba(0,0,0,0.60)' }
        : { background: 'linear-gradient(160deg, #ffffff 0%, #f8faff 100%)', boxShadow: '0 0 0 1px rgba(0,0,0,0.07), inset 0 1px 0 rgba(255,255,255,1), 0 4px 16px rgba(0,0,0,0.07)' }
    const cardHeaderStyle = dm
        ? { background: 'linear-gradient(90deg, #060b16 0%, #0a1020 100%)' }
        : { background: 'linear-gradient(90deg, #f9fafb 0%, #f0f4ff 100%)' }
    const [data, setData] = useState<CadenceSummaryResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [selectedDate, setSelectedDate] = useState(getTodayLocalDateString())
    const [error, setError] = useState('')

    const resolveBackendUrl = () => {
        const configured = process.env.NEXT_PUBLIC_PYTHON_API_URL
        if (configured && configured.trim()) return configured.trim()

        if (typeof window !== 'undefined') {
            const isLocal =
                window.location.hostname === 'localhost' ||
                window.location.hostname === '127.0.0.1'
            if (!isLocal) {
                // In deployed setups, prefer same-origin fallback over browser-localhost.
                return window.location.origin
            }
        }
        return 'http://localhost:8000'
    }

    useEffect(() => {
        const fetchSummary = async () => {
            if (!projectId) {
                setLoading(false)
                return
            }

            try {
                setLoading(true)
                setError('')
                const backendUrl = resolveBackendUrl()
                const todayLocal = getTodayLocalDateString()
                const safeDate = selectedDate > todayLocal ? todayLocal : selectedDate
                if (safeDate !== selectedDate) {
                    setSelectedDate(safeDate)
                }
                const dateParam = safeDate ? `?date=${safeDate}` : ''
                const response = await fetch(
                    `${backendUrl}/api/projects/${projectId}/cadence-summary${dateParam}`,
                    {
                        method: 'GET',
                        headers: {
                            Accept: 'application/json',
                            ...(backendToken ? { Authorization: `Bearer ${backendToken}` } : {}),
                        },
                    }
                )

                if (response.ok) {
                    const result: CadenceSummaryResponse = await response.json()
                    setData(result)
                } else {
                    setData(null)
                    setError(`Unable to load cadence summary (${response.status})`)
                }
            } catch (error) {
                setData(null)
                const message = error instanceof Error ? error.message : 'Failed to fetch'
                setError(`Cadence summary request failed: ${message}`)
                console.warn('Error fetching cadence summary:', error)
            } finally {
                setLoading(false)
            }
        }

        fetchSummary()
    }, [projectId, selectedDate, backendToken])

    if (loading) {
        return (
            <div className={`${dm ? "bg-[#080d1a]" : "bg-white border-gray-200"} rounded-xl border shadow-sm p-6`} style={cardStyle}>
                <div className="animate-pulse">
                    <div className={`h-6 ${dm ? "bg-white/[0.08]" : "bg-gray-200"} rounded w-1/3 mb-4`}></div>
                    <div className="space-y-3">
                        <div className={`h-10 ${dm ? "bg-white/[0.08]" : "bg-gray-200"} rounded`}></div>
                        <div className={`h-10 ${dm ? "bg-white/[0.08]" : "bg-gray-200"} rounded`}></div>
                        <div className={`h-10 ${dm ? "bg-white/[0.08]" : "bg-gray-200"} rounded`}></div>
                    </div>
                </div>
            </div>
        )
    }

    if (error) {
        return (
            <div className={`${dm ? "bg-[#080d1a]" : "bg-white border-gray-200"} rounded-xl border shadow-sm p-6`} style={cardStyle}>
                <div className="flex items-center justify-between mb-4">
                    <h2 className={`text-lg font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Cadence Summary</h2>
                    <DateNavigator value={selectedDate} onChange={setSelectedDate} dm={dm} />
                </div>
                <p className="text-sm text-red-600">{error}</p>
            </div>
        )
    }

    if (!data || data.status === 'no_data') {
        return (
            <div className={`${dm ? "bg-[#080d1a]" : "bg-white border-gray-200"} rounded-xl border shadow-sm p-6`} style={cardStyle}>
                <div className="flex items-center justify-between mb-4">
                    <h2 className={`text-lg font-semibold ${dm ? "text-gray-100" : "text-gray-900"} flex items-center gap-2`}>
                        <svg className="w-5 h-5 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25ZM6.75 12h.008v.008H6.75V12Zm0 3h.008v.008H6.75V15Zm0 3h.008v.008H6.75V18Z" />
                        </svg>
                        Cadence Summary
                    </h2>
                    <DateNavigator value={selectedDate} onChange={setSelectedDate} dm={dm} />
                </div>
                <p className="text-sm text-gray-500 text-center py-8">
                    No cadence summary available for {data?.date || 'today'}
                </p>
            </div>
        )
    }

    if (data.status === 'in_progress') {
        return (
            <div className={`${dm ? "bg-[#080d1a]" : "bg-white border-gray-200"} rounded-xl border shadow-sm p-6`} style={cardStyle}>
                <div className="flex items-center justify-between mb-4">
                    <h2 className={`text-lg font-semibold ${dm ? "text-gray-100" : "text-gray-900"} flex items-center gap-2`}>
                        <svg className="w-5 h-5 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25ZM6.75 12h.008v.008H6.75V12Zm0 3h.008v.008H6.75V15Zm0 3h.008v.008H6.75V18Z" />
                        </svg>
                        Cadence Summary
                    </h2>
                    <DateNavigator value={selectedDate} onChange={setSelectedDate} dm={dm} />
                </div>
                <div className="text-center py-8">
                    <div className={`inline-flex items-center gap-2 px-4 py-2 ${dm ? "bg-blue-900/20 border-blue-800/40" : "bg-blue-50 border-blue-200"} rounded-lg border`}>
                        <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                        <span className={`text-sm ${dm ? "text-blue-300" : "text-blue-700"}`}>Sessions in progress — summary will appear once all members respond</span>
                    </div>
                </div>
            </div>
        )
    }

    const summary = data.summary!
    const participation = summary.participation || { responded: [], not_responded: [], total: 0, rate: 0 }
    const memberUpdates = summary.member_updates || []
    const pmAttentionPoints = summary.pm_attention_points || []
    const nonResponders = (summary.non_responders || []).length
        ? summary.non_responders
        : (participation.not_responded || []).map(name => ({ member: name }))
    const attendanceRows: AttendanceEntry[] = (summary.attendance && summary.attendance.length)
        ? summary.attendance
        : [
            ...(participation.responded || []).map(name => ({ member: name, attended: true })),
            ...(participation.not_responded || []).map(name => ({ member: name, attended: false })),
        ]

    const attendees = attendanceRows.filter(item => item.attended)
    const absentees = attendanceRows.filter(item => !item.attended)

    const initials = (name: string) => name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()

    const statusChip = (status: string) => {
        const s = status.toLowerCase()
        const isBlue = s.includes('progress')
        const isGreen = s.includes('done') || s.includes('closed') || s.includes('complete')
        const bg = isBlue
            ? dm ? 'rgba(59,130,246,0.18)' : 'rgba(59,130,246,0.12)'
            : isGreen
                ? dm ? 'rgba(16,185,129,0.18)' : 'rgba(16,185,129,0.12)'
                : dm ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)'
        const color = isBlue ? '#60a5fa' : isGreen ? '#34d399' : dm ? '#9ca3af' : '#6b7280'
        return (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold flex-shrink-0" style={{ background: bg, color }}>
                {status}
            </span>
        )
    }

    // Section container with colored tinted header + task rows
    const sectionBlock = (
        title: string,
        accentColor: string,
        tasks: MemberTask[],
        emptyText: string,
        renderTask: (task: MemberTask, idx: number) => React.ReactNode,
        extraContent?: React.ReactNode
    ) => {
        const headerBg = dm
            ? `rgba(${accentColor === '#10b981' ? '16,185,129' : accentColor === '#3b82f6' ? '59,130,246' : accentColor === '#f59e0b' ? '245,158,11' : '129,140,248'}, 0.10)`
            : `rgba(${accentColor === '#10b981' ? '16,185,129' : accentColor === '#3b82f6' ? '59,130,246' : accentColor === '#f59e0b' ? '245,158,11' : '129,140,248'}, 0.07)`
        const dividerColor = dm ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)'
        const containerBorder = dm ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)'
        return (
            <div className="rounded-lg overflow-hidden" style={{ border: `1px solid ${containerBorder}` }}>
                {/* Section header */}
                <div className="px-4 py-2.5 flex items-center gap-2" style={{ background: headerBg, borderBottom: `1px solid ${dividerColor}` }}>
                    <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: accentColor }} />
                    <span className="text-xs font-bold uppercase tracking-widest" style={{ color: accentColor }}>{title}</span>
                </div>
                {/* Task rows */}
                {tasks.length === 0 && !extraContent ? (
                    <div className="px-4 py-3">
                        <p className={`text-sm italic ${dm ? "text-gray-500" : "text-gray-400"}`}>{emptyText}</p>
                    </div>
                ) : (
                    <div>
                        {tasks.map((task, idx) => (
                            <div key={`${task.key || task.title}-${idx}`} style={{ borderTop: idx > 0 ? `1px solid ${dividerColor}` : undefined }}>
                                {renderTask(task, idx)}
                            </div>
                        ))}
                        {extraContent}
                    </div>
                )}
            </div>
        )
    }

    return (
        <div className="space-y-4 pb-6">
            {/* Summary card */}
            <div className={`${dm ? "bg-[#080d1a]" : "bg-white"} rounded-xl border shadow-sm flex flex-col overflow-hidden`} style={cardStyle}>
                <div className={`px-6 py-4 border-b ${dm ? "border-white/[0.06]" : "border-gray-100"}`} style={cardHeaderStyle}>
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <svg className="w-5 h-5 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25ZM6.75 12h.008v.008H6.75V12Zm0 3h.008v.008H6.75V15Zm0 3h.008v.008H6.75V18Z" />
                            </svg>
                            <h2 className={`text-base font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>Cadence Summary</h2>
                            <span className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>— {summary.date}</span>
                        </div>
                        <DateNavigator value={selectedDate} onChange={setSelectedDate} dm={dm} />
                    </div>
                </div>
            </div>

            {/* 3-col: PM Attention, Attendees, Absentees */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                {/* PM Attention */}
                <div className={`${dm ? "bg-[#080d1a]" : "bg-white"} rounded-xl border shadow-sm overflow-hidden`} style={cardStyle}>
                    <div className={`px-5 py-3 border-b ${dm ? "border-white/[0.06]" : "border-gray-100"}`} style={cardHeaderStyle}>
                        <h3 className={`text-sm font-bold ${dm ? "text-gray-200" : "text-gray-800"} uppercase tracking-wide`}>PM Attention</h3>
                    </div>
                    <div className="px-5 py-4 space-y-2">
                        {pmAttentionPoints.length > 0 ? pmAttentionPoints.map((point, index) => (
                            <div key={`${index}-${point}`} className="flex gap-2.5">
                                <div className="mt-1.5 w-1.5 h-1.5 rounded-full bg-amber-400 flex-shrink-0" />
                                <p className={`text-sm ${dm ? "text-gray-300" : "text-gray-700"}`}>{point}</p>
                            </div>
                        )) : (
                            <p className={`text-sm italic ${dm ? "text-gray-500" : "text-gray-400"}`}>No critical points flagged.</p>
                        )}
                    </div>
                </div>

                {/* Attendees */}
                <div className={`${dm ? "bg-[#080d1a]" : "bg-white"} rounded-xl border shadow-sm overflow-hidden`} style={cardStyle}>
                    <div className={`px-5 py-3 border-b ${dm ? "border-white/[0.06]" : "border-gray-100"}`} style={cardHeaderStyle}>
                        <h3 className={`text-sm font-bold ${dm ? "text-gray-200" : "text-gray-800"} uppercase tracking-wide`}>
                            Attended <span className={`ml-1 text-xs font-semibold px-1.5 py-0.5 rounded-full ${dm ? "bg-emerald-900/40 text-emerald-400" : "bg-emerald-50 text-emerald-700"}`}>{attendees.length}</span>
                        </h3>
                    </div>
                    <div className="px-5 py-4 space-y-2">
                        {attendees.length > 0 ? attendees.map((item, index) => (
                            <div key={`${item.member}-${index}`} className="flex items-center gap-3">
                                <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0" style={{ background: 'rgba(120,165,48,0.15)', color: '#78a530', border: '1px solid rgba(120,165,48,0.25)' }}>
                                    {initials(item.member)}
                                </div>
                                <p className={`text-sm font-medium ${dm ? "text-gray-100" : "text-gray-900"}`}>{item.member}</p>
                            </div>
                        )) : (
                            <p className={`text-sm italic ${dm ? "text-gray-500" : "text-gray-400"}`}>No attendees in this run.</p>
                        )}
                    </div>
                </div>

                {/* Absentees */}
                <div className={`${dm ? "bg-[#080d1a]" : "bg-white"} rounded-xl border shadow-sm overflow-hidden`} style={cardStyle}>
                    <div className={`px-5 py-3 border-b ${dm ? "border-white/[0.06]" : "border-gray-100"}`} style={cardHeaderStyle}>
                        <h3 className={`text-sm font-bold ${dm ? "text-gray-200" : "text-gray-800"} uppercase tracking-wide`}>
                            Absent <span className={`ml-1 text-xs font-semibold px-1.5 py-0.5 rounded-full ${dm ? "bg-rose-900/40 text-rose-400" : "bg-rose-50 text-rose-700"}`}>{absentees.length}</span>
                        </h3>
                    </div>
                    <div className="px-5 py-4 space-y-2">
                        {absentees.length > 0 ? absentees.map((item, index) => (
                            <div key={`${item.member}-${index}`} className="flex items-start gap-3">
                                <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5" style={{ background: dm ? 'rgba(244,63,94,0.12)' : '#fff1f2', color: dm ? '#fb7185' : '#e11d48', border: `1px solid ${dm ? 'rgba(244,63,94,0.25)' : 'rgba(244,63,94,0.2)'}` }}>
                                    {initials(item.member)}
                                </div>
                                <div>
                                    <p className={`text-sm font-medium ${dm ? "text-gray-100" : "text-gray-900"}`}>{item.member}</p>
                                    <p className={`text-xs ${dm ? "text-gray-500" : "text-gray-400"}`}>
                                        {(nonResponders as NonResponder[]).find(nr => nr.member === item.member)?.reason || 'No response captured'}
                                    </p>
                                </div>
                            </div>
                        )) : (
                            <p className={`text-sm italic ${dm ? "text-gray-500" : "text-gray-400"}`}>No absentees in this run.</p>
                        )}
                    </div>
                </div>
            </div>

            {/* Member Updates */}
            <div className={`${dm ? "bg-[#080d1a]" : "bg-white"} rounded-xl border shadow-sm overflow-hidden`} style={cardStyle}>
                <div className={`px-6 py-4 border-b ${dm ? "border-white/[0.06]" : "border-gray-100"}`} style={cardHeaderStyle}>
                    <h3 className={`text-base font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>Member Updates</h3>
                </div>
                {memberUpdates.length > 0 ? (
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 p-4">
                        {memberUpdates.map(member => {
                            const scopedNotes = (member.notable_comments || []).filter(
                                note => String(note.tag || '').toLowerCase() !== 'overall'
                            )
                            const findTaskNotes = (task: MemberTask) => {
                                const taskTitle = String(task.title || '').trim().toLowerCase()
                                const taskKey = String(task.key || '').trim().toLowerCase()
                                return scopedNotes.filter(note => {
                                    const noteTitle = String(note.task_title || '').trim().toLowerCase()
                                    const noteKey = String(note.task_key || '').trim().toLowerCase()
                                    if (taskTitle && noteTitle && noteTitle === taskTitle) return true
                                    if (taskKey && noteKey && noteKey === taskKey) return true
                                    return false
                                }).slice(0, 2)
                            }

                            return (
                                <div key={member.member} className="rounded-xl overflow-hidden" style={{
                                    border: `1px solid ${dm ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)'}`,
                                    background: dm ? 'rgba(255,255,255,0.03)' : '#fafafa',
                                }}>
                                    {/* Member header */}
                                    <div className="flex items-center justify-between px-4 py-3" style={{
                                        borderBottom: `1px solid ${dm ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.07)'}`,
                                        background: dm ? 'rgba(120,165,48,0.06)' : 'rgba(120,165,48,0.05)',
                                    }}>
                                        <div className="flex items-center gap-3">
                                            <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0" style={{ background: 'rgba(120,165,48,0.15)', color: '#78a530', border: '1px solid rgba(120,165,48,0.3)' }}>
                                                {initials(member.member)}
                                            </div>
                                            <p className={`text-base font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>{member.member}</p>
                                        </div>
                                        {typeof member.response_time_minutes === 'number' && (
                                            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${dm ? "bg-white/[0.07] text-gray-400" : "bg-gray-100 text-gray-500"}`}>
                                                Responded in {member.response_time_minutes}m
                                            </span>
                                        )}
                                    </div>

                                    <div className="p-4 space-y-3">
                                        {/* Closed Tasks */}
                                        {sectionBlock(
                                            'Closed Tasks', '#10b981',
                                            member.closed_tasks || [],
                                            'No tasks closed.',
                                            (task, _idx) => {
                                                const taskNotes = findTaskNotes(task)
                                                return (
                                                    <div className="px-4 py-3">
                                                        <div className="flex items-start gap-2 flex-wrap">
                                                            {task.key && (
                                                                <span className={`text-xs font-mono font-semibold px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 ${dm ? "bg-white/[0.07] text-gray-400" : "bg-gray-100 text-gray-500"}`}>
                                                                    {task.key}
                                                                </span>
                                                            )}
                                                            <p className={`text-sm font-semibold flex-1 min-w-0 ${dm ? "text-gray-100" : "text-gray-900"}`}>{task.title}</p>
                                                            {task.status && statusChip(task.status)}
                                                        </div>
                                                        {taskNotes.map((note, noteIdx) => (
                                                            <p key={noteIdx} className={`mt-1.5 text-sm leading-relaxed ${dm ? "text-gray-400" : "text-gray-600"}`}>{note.comment}</p>
                                                        ))}
                                                    </div>
                                                )
                                            }
                                        )}

                                        {/* In Progress */}
                                        {sectionBlock(
                                            'In Progress', '#3b82f6',
                                            member.in_progress_tasks || [],
                                            'No in-progress tasks noted.',
                                            (task, _idx) => {
                                                const taskNotes = findTaskNotes(task)
                                                return (
                                                    <div className="px-4 py-3">
                                                        <div className="flex items-start gap-2 flex-wrap">
                                                            {task.key && (
                                                                <span className={`text-xs font-mono font-semibold px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 ${dm ? "bg-white/[0.07] text-gray-400" : "bg-gray-100 text-gray-500"}`}>
                                                                    {task.key}
                                                                </span>
                                                            )}
                                                            <p className={`text-sm font-semibold flex-1 min-w-0 ${dm ? "text-gray-100" : "text-gray-900"}`}>{task.title}</p>
                                                            {task.status && statusChip(task.status)}
                                                        </div>
                                                        {taskNotes.map((note, noteIdx) => (
                                                            <p key={noteIdx} className={`mt-1.5 text-sm leading-relaxed ${dm ? "text-gray-400" : "text-gray-600"}`}>{note.comment}</p>
                                                        ))}
                                                    </div>
                                                )
                                            }
                                        )}

                                        {/* Backlog Pickup */}
                                        {sectionBlock(
                                            'Backlog Pickup', '#f59e0b',
                                            member.backlog_pickups || [],
                                            member.backlog_action === 'deferred' ? 'Backlog items were deferred.' : 'No backlog pickup activity.',
                                            (task, _idx) => {
                                                const taskNotes = findTaskNotes(task)
                                                return (
                                                    <div className="px-4 py-3">
                                                        <div className="flex items-start gap-2 flex-wrap">
                                                            {task.key && (
                                                                <span className={`text-xs font-mono font-semibold px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 ${dm ? "bg-white/[0.07] text-gray-400" : "bg-gray-100 text-gray-500"}`}>
                                                                    {task.key}
                                                                </span>
                                                            )}
                                                            <p className={`text-sm font-semibold flex-1 min-w-0 ${dm ? "text-gray-100" : "text-gray-900"}`}>{task.title}</p>
                                                            {task.status && statusChip(task.status)}
                                                        </div>
                                                        {taskNotes.map((note, noteIdx) => (
                                                            <p key={noteIdx} className={`mt-1.5 text-sm leading-relaxed ${dm ? "text-gray-400" : "text-gray-600"}`}>{note.comment}</p>
                                                        ))}
                                                    </div>
                                                )
                                            }
                                        )}

                                        {/* Important Note */}
                                        {member.overall_update && sectionBlock(
                                            'Important Note', '#818cf8',
                                            [],
                                            '',
                                            () => null,
                                            <div className="px-4 py-3">
                                                <p className={`text-sm leading-relaxed ${dm ? "text-gray-200" : "text-gray-800"}`}>{member.overall_update}</p>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                ) : (
                    <div className="px-6 py-8 text-center">
                        <p className={`text-sm italic ${dm ? "text-gray-500" : "text-gray-400"}`}>No member updates available for this date.</p>
                    </div>
                )}
            </div>
        </div>
    )
}

/* Day-by-day date navigator with direct calendar jump */
function DateNavigator({ value, onChange, dm = false }: { value: string; onChange: (v: string) => void; dm?: boolean }) {
    const today = getTodayLocalDateString()
    const safeValue = value || today
    const isAtOrAfterToday = safeValue >= today

    const handlePrev = () => {
        onChange(shiftDate(safeValue, -1))
    }

    const handleNext = () => {
        if (isAtOrAfterToday) return
        const candidate = shiftDate(safeValue, 1)
        onChange(candidate > today ? today : candidate)
    }

    const handleCalendarChange = (nextValue: string) => {
        if (!nextValue) return
        onChange(nextValue > today ? today : nextValue)
    }

    return (
        <div className="flex items-center gap-2">
            <button
                type="button"
                onClick={handlePrev}
                className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border ${dm ? "border-white/[0.10] text-gray-400 hover:bg-white/[0.06]" : "border-gray-300 text-gray-600 hover:bg-gray-50"}`}
                aria-label="Previous day"
                title="Previous day"
            >
                <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fillRule="evenodd" d="M12.78 4.22a.75.75 0 0 1 0 1.06L8.06 10l4.72 4.72a.75.75 0 1 1-1.06 1.06l-5.25-5.25a.75.75 0 0 1 0-1.06l5.25-5.25a.75.75 0 0 1 1.06 0Z" clipRule="evenodd" />
                </svg>
            </button>

            <div className="relative">
                <input
                    type="date"
                    value={safeValue}
                    max={today}
                    onChange={e => handleCalendarChange(e.target.value)}
                    className={`h-8 rounded-lg border px-2 text-xs focus:outline-none focus:ring-1 focus:ring-[#78a530] focus:border-[#78a530] ${dm ? "bg-[#111520] border-white/[0.10] text-gray-300" : "border-gray-300 text-gray-600"}`}
                    aria-label="Select date"
                />
            </div>

            <button
                type="button"
                onClick={handleNext}
                disabled={isAtOrAfterToday}
                className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border ${dm ? "border-white/[0.10] text-gray-400 hover:bg-white/[0.06]" : "border-gray-300 text-gray-600 hover:bg-gray-50"} disabled:cursor-not-allowed disabled:opacity-40`}
                aria-label="Next day"
                title="Next day"
            >
                <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fillRule="evenodd" d="M7.22 15.78a.75.75 0 0 1 0-1.06L11.94 10 7.22 5.28a.75.75 0 1 1 1.06-1.06l5.25 5.25a.75.75 0 0 1 0 1.06l-5.25 5.25a.75.75 0 0 1-1.06 0Z" clipRule="evenodd" />
                </svg>
            </button>

            <button
                type="button"
                onClick={() => onChange(today)}
                disabled={safeValue === today}
                className={`h-8 rounded-lg border px-2 text-xs ${dm ? "border-white/[0.10] text-gray-400 hover:bg-white/[0.06]" : "border-gray-300 text-gray-600 hover:bg-gray-50"} disabled:cursor-not-allowed disabled:opacity-40`}
            >
                Today
            </button>
        </div>
    )
}
