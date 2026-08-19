'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useSession } from "next-auth/react"

interface HeatmapData {
    assignee_name: string
    total_activity: number
    daily_counts: number[]
}

interface TeamActivityHeatmapProps {
    projectId: string
    viewMode?: "weekly" | "monthly"
    isDarkMode?: boolean
}

// Returns Monday of the week containing the given date
function getMondayOf(date: Date): Date {
    const d = new Date(date)
    const day = d.getDay() // 0=Sun, 1=Mon, ...
    const diff = day === 0 ? -6 : 1 - day
    d.setDate(d.getDate() + diff)
    d.setHours(0, 0, 0, 0)
    return d
}

function formatISODate(date: Date): string {
    return date.toISOString().split('T')[0]
}

function addDays(date: Date, days: number): Date {
    const d = new Date(date)
    d.setDate(d.getDate() + days)
    return d
}

// e.g. "Feb 17 – Feb 23, 2026"
function formatWeekRange(monday: Date): string {
    const sunday = addDays(monday, 6)
    const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' }
    const start = monday.toLocaleDateString('en-US', opts)
    const end = sunday.toLocaleDateString('en-US', { ...opts, year: 'numeric' })
    return `${start} – ${end}`
}

// Build a calendar grid for a month: array of weeks, each week is 7 Date|null entries
function buildCalendarMonth(year: number, month: number): (Date | null)[][] {
    const firstDay = new Date(year, month, 1)
    const lastDay = new Date(year, month + 1, 0)
    // Adjust so week starts on Monday (0=Mon)
    const startOffset = (firstDay.getDay() + 6) % 7
    const weeks: (Date | null)[][] = []
    let week: (Date | null)[] = Array(startOffset).fill(null)

    for (let d = 1; d <= lastDay.getDate(); d++) {
        week.push(new Date(year, month, d))
        if (week.length === 7) {
            weeks.push(week)
            week = []
        }
    }
    if (week.length > 0) {
        while (week.length < 7) week.push(null)
        weeks.push(week)
    }
    return weeks
}

// Returns inline background color with intensity scale
function getCellBg(count: number, maxCount: number, dm: boolean): string {
    if (count === 0) return dm ? 'rgba(255,255,255,0.04)' : '#f3f4f6'
    const ratio = maxCount > 0 ? count / maxCount : 0
    if (!dm) {
        if (ratio < 0.25) return '#ddf0ab'
        if (ratio < 0.5)  return '#b8de60'
        if (ratio < 0.75) return '#96c93d'
        return '#78a530'
    }
    if (ratio < 0.25) return 'rgba(120,165,48,0.28)'
    if (ratio < 0.5)  return 'rgba(120,165,48,0.52)'
    if (ratio < 0.75) return 'rgba(120,165,48,0.74)'
    return '#78a530'
}

function getCellFg(count: number, maxCount: number, dm: boolean): string {
    if (count === 0) return dm ? 'rgba(255,255,255,0.18)' : '#9ca3af'
    const ratio = maxCount > 0 ? count / maxCount : 0
    if (!dm) return ratio < 0.5 ? '#3a6010' : '#ffffff'
    return ratio < 0.35 ? '#a3e635' : '#ffffff'
}

// Border: light mode uses intensity-scaled green border to show activity level
function getCellBorder(count: number, maxCount: number, dm: boolean): string {
    if (count === 0) return dm ? 'rgba(255,255,255,0.07)' : '#e5e7eb'
    if (dm) return 'rgba(120,165,48,0.35)'
    const ratio = maxCount > 0 ? count / maxCount : 0
    if (ratio < 0.25) return 'rgba(120,165,48,0.50)'
    if (ratio < 0.5)  return 'rgba(120,165,48,0.75)'
    if (ratio < 0.75) return '#78a530'
    return '#5e8224'
}

export default function TeamActivityHeatmap({ projectId, viewMode = "weekly", isDarkMode = false }: TeamActivityHeatmapProps) {
    const dm = isDarkMode
    const { data: session } = useSession()
    const backendToken = String(session?.user?.backendToken || "").trim()
    const cardStyle = dm
        ? { background: 'linear-gradient(160deg, #080d1a 0%, #0d1225 50%, #080c18 100%)', boxShadow: '0 0 0 1px rgba(255,255,255,0.12), inset 0 1px 0 rgba(255,255,255,0.08), 0 8px 40px rgba(0,0,0,0.60)' }
        : { background: 'linear-gradient(160deg, #ffffff 0%, #f8faff 100%)', boxShadow: '0 0 0 1px rgba(0,0,0,0.07), inset 0 1px 0 rgba(255,255,255,1), 0 4px 16px rgba(0,0,0,0.07)' }
    const cardHeaderStyle = dm
        ? { background: 'linear-gradient(90deg, #060b16 0%, #0a1020 100%)' }
        : { background: '#f9fafb' }

    const today = new Date()
    const currentMonday = getMondayOf(today)
    const currentMonth = new Date(today.getFullYear(), today.getMonth(), 1)

    const [selectedWeek, setSelectedWeek] = useState<Date>(currentMonday)
    const [selectedMonth, setSelectedMonth] = useState<Date>(currentMonth)
    const [heatmapData, setHeatmapData] = useState<HeatmapData[]>([])
    const [weekDates, setWeekDates] = useState<string[]>([])
    const [totalActivity, setTotalActivity] = useState(0)
    const [loading, setLoading] = useState(true)
    const [showCalendar, setShowCalendar] = useState(false)
    const [calMonth, setCalMonth] = useState<Date>(new Date(currentMonday.getFullYear(), currentMonday.getMonth(), 1))
    const calendarRef = useRef<HTMLDivElement>(null)

    // Close calendar on outside click
    useEffect(() => {
        function handleClick(e: MouseEvent) {
            if (calendarRef.current && !calendarRef.current.contains(e.target as Node)) {
                setShowCalendar(false)
            }
        }
        if (showCalendar) document.addEventListener('mousedown', handleClick)
        return () => document.removeEventListener('mousedown', handleClick)
    }, [showCalendar])

    useEffect(() => {
        const fetchHeatmapData = async () => {
            if (!projectId) return
            try {
                setLoading(true)
                const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || 'http://localhost:8000'

                if (viewMode === "weekly") {
                    const weekParam = formatISODate(selectedWeek)
                    const response = await fetch(
                        `${backendUrl}/api/projects/${projectId}/team-activity-heatmap?week_start=${weekParam}`,
                        {
                            credentials: 'include',
                            headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
                        }
                    )
                    if (response.ok) {
                        const data = await response.json()
                        setHeatmapData(data.heatmap_data || [])
                        setWeekDates(data.week_dates || [])
                        setTotalActivity(data.total_team_activity || 0)
                    }
                } else {
                    const monthStart = new Date(selectedMonth.getFullYear(), selectedMonth.getMonth(), 1)
                    const firstMonday = getMondayOf(monthStart)

                    const promises = []
                    let currentWeekStart = firstMonday
                    for (let i = 0; i < 5; i++) {
                        const weekParam = formatISODate(currentWeekStart)
                        promises.push(
                            fetch(
                                `${backendUrl}/api/projects/${projectId}/team-activity-heatmap?week_start=${weekParam}`,
                                {
                                    credentials: 'include',
                                    headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
                                }
                            ).then(res => res.ok ? res.json() : null)
                        )
                        currentWeekStart = addDays(currentWeekStart, 7)
                    }

                    const results = await Promise.all(promises)
                    const allDates: string[] = []
                    const combinedData = new Map<string, number[]>()

                    results.forEach(result => {
                        if (result) {
                            result.week_dates?.forEach((date: string) => {
                                if (!allDates.includes(date)) allDates.push(date)
                            })
                            result.heatmap_data?.forEach((member: HeatmapData) => {
                                if (!combinedData.has(member.assignee_name)) {
                                    combinedData.set(member.assignee_name, [])
                                }
                                combinedData.get(member.assignee_name)!.push(...member.daily_counts)
                            })
                        }
                    })

                    setWeekDates(allDates)
                    setHeatmapData(
                        Array.from(combinedData.entries()).map(([name, counts]) => ({
                            assignee_name: name,
                            daily_counts: counts,
                            total_activity: counts.reduce((a, b) => a + b, 0)
                        }))
                    )
                    setTotalActivity(Array.from(combinedData.values()).flat().reduce((a, b) => a + b, 0))
                }
            } catch (error) {
                console.error('Error fetching team activity heatmap:', error)
            } finally {
                setLoading(false)
            }
        }
        fetchHeatmapData()
    }, [projectId, selectedWeek, selectedMonth, viewMode, backendToken])

    const maxCount = Math.max(...heatmapData.flatMap(m => m.daily_counts), 1)
    const isCurrentWeek = formatISODate(selectedWeek) === formatISODate(currentMonday)

    // Sort members by total activity descending
    const sortedMembers = [...heatmapData].sort((a, b) => b.total_activity - a.total_activity)
    const maxTotalActivity = sortedMembers[0]?.total_activity || 1

    // Peak day computation
    const dayTotals = weekDates.slice(0, 7).map((date, i) => ({
        date,
        total: heatmapData.reduce((sum, m) => sum + (m.daily_counts[i] || 0), 0)
    }))
    const peakDay = dayTotals.reduce((best, d) => d.total > best.total ? d : best, { date: '', total: 0 })
    const peakDayLabel = peakDay.date
        ? new Date(peakDay.date + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' })
        : '—'

    const activeMembers = heatmapData.filter(m => m.total_activity > 0).length

    function prevWeek() { setSelectedWeek(w => addDays(w, -7)) }
    function nextWeek() { if (!isCurrentWeek) setSelectedWeek(w => addDays(w, 7)) }

    function prevMonth() { setSelectedMonth(m => new Date(m.getFullYear(), m.getMonth() - 1, 1)) }
    function nextMonth() {
        const currentMonthDate = new Date(today.getFullYear(), today.getMonth(), 1)
        if (formatISODate(selectedMonth) !== formatISODate(currentMonthDate)) {
            setSelectedMonth(m => new Date(m.getFullYear(), m.getMonth() + 1, 1))
        }
    }

    const isCurrentMonth = selectedMonth.getFullYear() === today.getFullYear() &&
                          selectedMonth.getMonth() === today.getMonth()
    const monthLabel = selectedMonth.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

    function formatColHeader(isoDate: string): { day: string; date: string } {
        const d = new Date(isoDate + 'T00:00:00')
        return {
            day: d.toLocaleDateString('en-US', { weekday: 'short' }),
            date: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
        }
    }

    function formatTooltip(count: number, isoDate: string): string {
        if (count === 0) return 'No status changes'
        const d = new Date(isoDate + 'T00:00:00')
        const label = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
        return `${count} status ${count === 1 ? 'change' : 'changes'} on ${label}`
    }

    const calWeeks = buildCalendarMonth(calMonth.getFullYear(), calMonth.getMonth())
    const calMonthLabel = calMonth.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

    function handleCalDayClick(day: Date) {
        const monday = getMondayOf(day)
        if (monday > currentMonday) return
        setSelectedWeek(monday)
        setShowCalendar(false)
    }

    function isInSelectedWeek(day: Date): boolean {
        const monday = getMondayOf(day)
        return formatISODate(monday) === formatISODate(selectedWeek)
    }

    if (loading) {
        return (
            <div className="rounded-xl border" style={cardStyle}>
                <div className="p-6 animate-pulse">
                    <div className={`h-5 ${dm ? "bg-white/[0.08]" : "bg-gray-200"} rounded w-1/3 mb-4`}></div>
                    <div className="grid grid-cols-3 gap-3 mb-6">
                        {[1,2,3].map(i => <div key={i} className={`h-16 ${dm ? "bg-white/[0.06]" : "bg-gray-100"} rounded-lg`}></div>)}
                    </div>
                    <div className="space-y-3">
                        {[1,2,3].map(i => <div key={i} className={`h-14 ${dm ? "bg-white/[0.06]" : "bg-gray-100"} rounded-lg`}></div>)}
                    </div>
                </div>
            </div>
        )
    }

    return (
        <div className="rounded-xl border flex flex-col overflow-hidden" style={cardStyle}>
            {/* Header */}
            <div className={`px-6 py-4 border-b ${dm ? "border-white/[0.06]" : "border-[#d4edaa]"}`} style={cardHeaderStyle}>
                <div className="flex items-center justify-between flex-wrap gap-3">
                    <div>
                        <h2 className={`text-lg font-bold ${dm ? "text-gray-100" : "text-gray-900"} flex items-center gap-2`}>
                            <svg className="w-5 h-5 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
                            </svg>
                            Team Activity
                        </h2>
                        <p className={`text-sm mt-0.5 ${dm ? "text-gray-400" : "text-gray-700"}`}>
                            <span className="font-semibold text-[#78a530]">{totalActivity}</span>
                            {' '}status {totalActivity === 1 ? 'change' : 'changes'} · {viewMode === "weekly" ? formatWeekRange(selectedWeek) : monthLabel}
                        </p>
                    </div>

                    {/* Navigator */}
                    <div className="flex items-center gap-2 relative" ref={calendarRef}>
                        {viewMode === "weekly" ? (
                            <>
                                <button
                                    onClick={prevWeek}
                                    className={`p-1.5 rounded-lg ${dm ? "hover:bg-white/[0.06] text-gray-400 hover:text-gray-200" : "hover:bg-[#eef7dc] text-gray-500 hover:text-gray-800"} transition-colors`}
                                    title="Previous week"
                                >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
                                    </svg>
                                </button>

                                <button
                                    onClick={() => {
                                        setCalMonth(new Date(selectedWeek.getFullYear(), selectedWeek.getMonth(), 1))
                                        setShowCalendar(v => !v)
                                    }}
                                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${dm ? "border-white/[0.08] text-gray-300 hover:border-[#78a530] hover:bg-[#78a530]/10" : "border-[#c8e68a] text-gray-700 hover:border-[#78a530] hover:bg-[#f0f9e0]"}`}
                                >
                                    <svg className="w-4 h-4 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
                                    </svg>
                                    {isCurrentWeek ? 'This week' : formatWeekRange(selectedWeek)}
                                </button>

                                <button
                                    onClick={nextWeek}
                                    disabled={isCurrentWeek}
                                    className={`p-1.5 rounded-lg transition-colors ${isCurrentWeek ? (dm ? 'text-gray-600 cursor-not-allowed' : 'text-gray-300 cursor-not-allowed') : (dm ? 'hover:bg-white/[0.06] text-gray-400 hover:text-gray-200' : 'hover:bg-[#eef7dc] text-gray-500 hover:text-gray-800')}`}
                                    title="Next week"
                                >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                                    </svg>
                                </button>
                            </>
                        ) : (
                            <>
                                <button
                                    onClick={prevMonth}
                                    className={`p-1.5 rounded-lg ${dm ? "hover:bg-white/[0.06] text-gray-400 hover:text-gray-200" : "hover:bg-[#eef7dc] text-gray-500 hover:text-gray-800"} transition-colors`}
                                    title="Previous month"
                                >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
                                    </svg>
                                </button>

                                <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm font-medium ${dm ? "border-white/[0.08] text-gray-300" : "border-[#c8e68a] text-gray-700"}`}>
                                    <svg className="w-4 h-4 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
                                    </svg>
                                    {isCurrentMonth ? 'This month' : monthLabel}
                                </div>

                                <button
                                    onClick={nextMonth}
                                    disabled={isCurrentMonth}
                                    className={`p-1.5 rounded-lg transition-colors ${isCurrentMonth ? (dm ? 'text-gray-600 cursor-not-allowed' : 'text-gray-300 cursor-not-allowed') : (dm ? 'hover:bg-white/[0.06] text-gray-400 hover:text-gray-200' : 'hover:bg-[#eef7dc] text-gray-500 hover:text-gray-800')}`}
                                    title="Next month"
                                >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                                    </svg>
                                </button>
                            </>
                        )}

                        {/* Calendar picker dropdown */}
                        {showCalendar && (
                            <div className={`absolute top-full right-0 mt-2 z-50 border rounded-xl shadow-xl p-4 w-72 ${dm ? "border-white/[0.08]" : "border-gray-200"}`}
                                style={dm ? { background: '#080d1a' } : { background: '#ffffff' }}>
                                <div className="flex items-center justify-between mb-3">
                                    <button
                                        onClick={() => setCalMonth(m => new Date(m.getFullYear(), m.getMonth() - 1, 1))}
                                        className={`p-1 rounded ${dm ? "hover:bg-white/[0.06] text-gray-400" : "hover:bg-gray-100 text-gray-500"}`}
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
                                        </svg>
                                    </button>
                                    <span className={`text-sm font-semibold ${dm ? "text-gray-200" : "text-gray-800"}`}>{calMonthLabel}</span>
                                    <button
                                        onClick={() => setCalMonth(m => new Date(m.getFullYear(), m.getMonth() + 1, 1))}
                                        className={`p-1 rounded ${dm ? "hover:bg-white/[0.06] text-gray-400" : "hover:bg-gray-100 text-gray-500"}`}
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                                        </svg>
                                    </button>
                                </div>

                                <div className="grid grid-cols-7 mb-1">
                                    {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => (
                                        <div key={i} className="text-center text-xs font-semibold text-gray-400 py-1">{d}</div>
                                    ))}
                                </div>

                                {calWeeks.map((week, wi) => (
                                    <div key={wi} className="grid grid-cols-7">
                                        {week.map((day, di) => {
                                            if (!day) return <div key={di} />
                                            const isFuture = getMondayOf(day) > currentMonday
                                            const inSelected = isInSelectedWeek(day)
                                            return (
                                                <button
                                                    key={di}
                                                    onClick={() => handleCalDayClick(day)}
                                                    disabled={isFuture}
                                                    className={`text-xs py-1.5 rounded transition-colors text-center
                                                        ${isFuture ? (dm ? 'text-gray-600 cursor-not-allowed' : 'text-gray-300 cursor-not-allowed') : (dm ? 'hover:bg-white/[0.06] cursor-pointer' : 'hover:bg-[#f0f9e0] cursor-pointer')}
                                                        ${inSelected ? 'bg-[#78a530] text-white hover:bg-[#78a530]' : (dm ? 'text-gray-300' : 'text-gray-700')}
                                                    `}
                                                >
                                                    {day.getDate()}
                                                </button>
                                            )
                                        })}
                                    </div>
                                ))}

                                {!isCurrentWeek && (
                                    <button
                                        onClick={() => { setSelectedWeek(currentMonday); setShowCalendar(false) }}
                                        className="mt-3 w-full text-xs text-[#78a530] font-medium hover:underline"
                                    >
                                        Jump to current week
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Heatmap Content */}
            <div className="p-6 overflow-x-auto">
                {heatmapData.length === 0 ? (
                    <div className="text-center py-12">
                        <div className={`w-12 h-12 rounded-full mx-auto mb-4 flex items-center justify-center ${dm ? "bg-white/[0.06]" : "bg-gray-100"}`}>
                            <svg className="w-6 h-6 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75Z" />
                            </svg>
                        </div>
                        <p className={`text-sm font-semibold ${dm ? "text-gray-300" : "text-gray-700"}`}>No activity recorded {viewMode === "weekly" ? "this week" : "this month"}</p>
                        <p className={`text-xs mt-1 ${dm ? "text-gray-500" : "text-gray-700"}`}>Data appears here when tasks move status in Jira</p>
                    </div>
                ) : viewMode === "weekly" ? (
                    <div className="min-w-[640px] space-y-5">
                        {/* Summary stats */}
                        <div className="grid grid-cols-3 gap-3">
                            {[
                                {
                                    label: 'Total Changes',
                                    value: totalActivity,
                                    icon: (
                                        <svg className="w-4 h-4 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 21 3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" />
                                        </svg>
                                    )
                                },
                                {
                                    label: 'Active Members',
                                    value: activeMembers,
                                    sub: `of ${heatmapData.length}`,
                                    icon: (
                                        <svg className="w-4 h-4 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
                                        </svg>
                                    )
                                },
                                {
                                    label: 'Peak Day',
                                    value: peakDayLabel,
                                    sub: peakDay.total > 0 ? `${peakDay.total} changes` : '',
                                    icon: (
                                        <svg className="w-4 h-4 text-[#78a530]" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18 9 11.25l4.306 4.306a11.95 11.95 0 0 1 5.814-5.518l2.74-1.22m0 0-5.94-2.281m5.94 2.28-2.28 5.941" />
                                        </svg>
                                    )
                                }
                            ].map((stat, i) => (
                                <motion.div
                                    key={stat.label}
                                    initial={{ opacity: 0, y: 8 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: i * 0.07 }}
                                    className="rounded-lg px-4 py-3"
                                    style={dm
                                        ? { background: 'linear-gradient(160deg, #080d1a 0%, #0d1225 50%, #080c18 100%)', boxShadow: '0 0 0 1px rgba(255,255,255,0.10), inset 0 1px 0 rgba(255,255,255,0.06), 0 4px 16px rgba(0,0,0,0.50)' }
                                        : { background: '#ffffff', borderLeft: '4px solid #78a530', border: '1px solid #e5e7eb', borderLeftWidth: '4px', borderLeftColor: '#78a530' }
                                    }
                                >
                                    <div className="flex items-center gap-1.5 mb-1.5">
                                        {stat.icon}
                                        <span className={`text-xs font-semibold uppercase tracking-wide ${dm ? "text-gray-400" : "text-gray-700"}`}>{stat.label}</span>
                                    </div>
                                    <div className="flex items-baseline gap-1.5">
                                        <span className={`text-2xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>{stat.value}</span>
                                        {stat.sub && <span className={`text-xs ${dm ? "text-gray-500" : "text-gray-700"}`}>{stat.sub}</span>}
                                    </div>
                                </motion.div>
                            ))}
                        </div>

                        {/* Column headers */}
                        <div className="grid grid-cols-[200px_repeat(7,1fr)] gap-2">
                            <div className={`text-xs font-bold uppercase tracking-wide ${dm ? "text-gray-400" : "text-gray-700"}`}>Member</div>
                            {weekDates.slice(0, 7).map((isoDate) => {
                                const { day, date } = formatColHeader(isoDate)
                                const dayTotal = dayTotals.find(d => d.date === isoDate)?.total || 0
                                return (
                                    <div key={isoDate} className="text-center">
                                        <div className={`text-xs font-bold ${dm ? "text-gray-300" : "text-gray-700"}`}>{day}</div>
                                        <div className={`text-xs ${dm ? "text-gray-500" : "text-gray-700"}`}>{date}</div>
                                        {dayTotal > 0 && (
                                            <div className="mt-1 flex justify-center">
                                                <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: 'rgba(120,165,48,0.18)', color: '#78a530' }}>
                                                    {dayTotal}
                                                </span>
                                            </div>
                                        )}
                                    </div>
                                )
                            })}
                        </div>

                        {/* Heatmap rows */}
                        <div className="space-y-2">
                            <AnimatePresence>
                                {sortedMembers.map((member, rowIdx) => (
                                    <motion.div
                                        key={member.assignee_name}
                                        initial={{ opacity: 0, x: -12 }}
                                        animate={{ opacity: 1, x: 0 }}
                                        transition={{ delay: rowIdx * 0.05, duration: 0.25 }}
                                        className="grid grid-cols-[200px_repeat(7,1fr)] gap-2 items-center group"
                                    >
                                        {/* Member name + activity bar */}
                                        <div className="flex items-center gap-2 pr-2">
                                            <div
                                                className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0"
                                                style={member.total_activity > 0
                                                    ? { background: 'rgba(120,165,48,0.2)', color: '#78a530', border: '1.5px solid rgba(120,165,48,0.35)' }
                                                    : { background: dm ? 'rgba(255,255,255,0.07)' : '#f3f4f6', color: dm ? '#9ca3af' : '#6b7280' }
                                                }
                                            >
                                                {member.assignee_name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()}
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <p className={`text-sm font-semibold truncate ${dm ? "text-gray-100" : "text-gray-800"}`}>{member.assignee_name}</p>
                                                <div className="flex items-center gap-1.5 mt-0.5">
                                                    <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: dm ? 'rgba(255,255,255,0.08)' : '#e5e7eb' }}>
                                                        <motion.div
                                                            className="h-full rounded-full"
                                                            style={{ background: '#78a530' }}
                                                            initial={{ width: 0 }}
                                                            animate={{ width: `${Math.round((member.total_activity / maxTotalActivity) * 100)}%` }}
                                                            transition={{ delay: rowIdx * 0.05 + 0.15, duration: 0.4 }}
                                                        />
                                                    </div>
                                                    <span className={`text-[10px] font-medium shrink-0 ${dm ? "text-gray-500" : "text-gray-700"}`}>{member.total_activity}</span>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Activity cells */}
                                        {member.daily_counts.slice(0, 7).map((count, dayIndex) => {
                                            const isoDate = weekDates[dayIndex] ?? ''
                                            return (
                                                <div
                                                    key={dayIndex}
                                                    className="h-12 rounded-lg flex items-center justify-center transition-all hover:scale-105 hover:shadow-lg cursor-pointer group/cell relative"
                                                    style={{
                                                        background: getCellBg(count, maxCount, dm),
                                                        border: `1px solid ${getCellBorder(count, maxCount, dm)}`,
                                                    }}
                                                >
                                                    <span className="text-xs font-bold" style={{ color: getCellFg(count, maxCount, dm) }}>
                                                        {count > 0 ? count : ''}
                                                    </span>
                                                    {/* Tooltip */}
                                                    <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-gray-900 text-white text-xs rounded-md opacity-0 group-hover/cell:opacity-100 transition-opacity whitespace-nowrap pointer-events-none z-10 shadow-lg">
                                                        {formatTooltip(count, isoDate)}
                                                    </div>
                                                </div>
                                            )
                                        })}
                                    </motion.div>
                                ))}
                            </AnimatePresence>
                        </div>

                        {/* Legend */}
                        <div className={`pt-4 border-t ${dm ? "border-white/[0.08]" : "border-gray-200"} flex items-center gap-6 flex-wrap`}>
                            <span className={`text-xs font-semibold uppercase tracking-wide ${dm ? "text-gray-500" : "text-gray-700"}`}>Intensity</span>
                            <div className="flex items-center gap-1.5">
                                <div className="w-6 h-6 rounded" style={{ background: getCellBg(0, 10, dm), border: `1px solid ${getCellBorder(0, 10, dm)}` }}></div>
                                <span className={`text-xs ${dm ? "text-gray-400" : "text-gray-700"}`}>None</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                                <div className="w-6 h-6 rounded" style={{ background: getCellBg(2, 10, dm), border: `1px solid ${getCellBorder(2, 10, dm)}` }}></div>
                                <span className={`text-xs ${dm ? "text-gray-400" : "text-gray-700"}`}>Low</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                                <div className="w-6 h-6 rounded" style={{ background: getCellBg(5, 10, dm), border: `1px solid ${getCellBorder(5, 10, dm)}` }}></div>
                                <span className={`text-xs ${dm ? "text-gray-400" : "text-gray-700"}`}>Medium</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                                <div className="w-6 h-6 rounded" style={{ background: getCellBg(8, 10, dm), border: `1px solid ${getCellBorder(8, 10, dm)}` }}></div>
                                <span className={`text-xs ${dm ? "text-gray-400" : "text-gray-700"}`}>High</span>
                            </div>
                            <div className="flex items-center gap-1.5">
                                <div className="w-6 h-6 rounded" style={{ background: getCellBg(10, 10, dm), border: `1px solid ${getCellBorder(10, 10, dm)}` }}></div>
                                <span className={`text-xs ${dm ? "text-gray-400" : "text-gray-700"}`}>Peak</span>
                            </div>
                        </div>
                    </div>
                ) : (
                    /* Monthly Calendar View */
                    <div className="space-y-5">
                        {/* Summary stats for monthly */}
                        <div className="grid grid-cols-3 gap-3">
                            {[
                                { label: 'Total Changes', value: totalActivity },
                                { label: 'Active Members', value: activeMembers, sub: `of ${heatmapData.length}` },
                                { label: 'Top Contributor', value: sortedMembers[0]?.assignee_name.split(' ')[0] || '—', sub: sortedMembers[0] ? `${sortedMembers[0].total_activity} changes` : '' }
                            ].map((stat, i) => (
                                <motion.div
                                    key={stat.label}
                                    initial={{ opacity: 0, y: 8 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: i * 0.07 }}
                                    className="rounded-lg px-4 py-3"
                                    style={dm
                                        ? { background: 'linear-gradient(160deg, #080d1a 0%, #0d1225 50%, #080c18 100%)', boxShadow: '0 0 0 1px rgba(255,255,255,0.10), inset 0 1px 0 rgba(255,255,255,0.06), 0 4px 16px rgba(0,0,0,0.50)' }
                                        : { background: '#ffffff', borderLeft: '4px solid #78a530', border: '1px solid #e5e7eb', borderLeftWidth: '4px', borderLeftColor: '#78a530' }
                                    }
                                >
                                    <div className={`text-xs font-semibold uppercase tracking-wide mb-1 ${dm ? "text-gray-400" : "text-gray-700"}`}>{stat.label}</div>
                                    <div className="flex items-baseline gap-1.5">
                                        <span className={`text-xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>{stat.value}</span>
                                        {stat.sub && <span className={`text-xs ${dm ? "text-gray-500" : "text-gray-700"}`}>{stat.sub}</span>}
                                    </div>
                                </motion.div>
                            ))}
                        </div>

                        {sortedMembers.map((member, memberIdx) => (
                            <motion.div
                                key={member.assignee_name}
                                initial={{ opacity: 0, y: 12 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: memberIdx * 0.08 }}
                                className="rounded-xl overflow-hidden"
                                style={dm
                                    ? { background: 'linear-gradient(160deg, #080d1a 0%, #0d1225 50%, #080c18 100%)', boxShadow: '0 0 0 1px rgba(255,255,255,0.10), inset 0 1px 0 rgba(255,255,255,0.06), 0 4px 16px rgba(0,0,0,0.50)' }
                                    : { background: '#ffffff', border: '1px solid #e5e7eb', borderLeft: '4px solid #78a530', borderLeftWidth: '4px', borderLeftColor: '#78a530' }
                                }
                            >
                                {/* Member header */}
                                <div className={`px-4 py-3 flex items-center gap-3 border-b ${dm ? "border-white/[0.06]" : "border-gray-200"}`}
                                    style={dm ? { background: 'linear-gradient(90deg, #060b16 0%, #0a1020 100%)' } : { background: '#f9fafb' }}>
                                    <div
                                        className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold shrink-0"
                                        style={member.total_activity > 0
                                            ? { background: 'rgba(120,165,48,0.2)', color: '#78a530', border: '1.5px solid rgba(120,165,48,0.35)' }
                                            : { background: dm ? 'rgba(255,255,255,0.08)' : '#e5e7eb', color: dm ? '#9ca3af' : '#6b7280' }
                                        }
                                    >
                                        {member.assignee_name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <p className={`text-sm font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>{member.assignee_name}</p>
                                        <p className={`text-xs ${dm ? "text-gray-500" : "text-gray-700"}`}>
                                            {member.total_activity} {member.total_activity === 1 ? 'change' : 'changes'} this month
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        <div className="w-24 h-1.5 rounded-full overflow-hidden" style={{ background: dm ? 'rgba(255,255,255,0.08)' : '#e5e7eb' }}>
                                            <motion.div
                                                className="h-full rounded-full"
                                                style={{ background: '#78a530' }}
                                                initial={{ width: 0 }}
                                                animate={{ width: `${Math.round((member.total_activity / maxTotalActivity) * 100)}%` }}
                                                transition={{ delay: memberIdx * 0.08 + 0.2, duration: 0.5 }}
                                            />
                                        </div>
                                    </div>
                                </div>

                                {/* Calendar grid */}
                                <div className="p-4">
                                    <div className="grid grid-cols-7 gap-1 mb-2">
                                        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(day => (
                                            <div key={day} className={`text-center text-xs font-semibold ${dm ? "text-gray-400" : "text-gray-700"}`}>
                                                {day}
                                            </div>
                                        ))}
                                    </div>

                                    <div className="grid grid-cols-7 gap-1">
                                        {weekDates.map((isoDate, idx) => {
                                            const count = member.daily_counts[idx] || 0
                                            const date = new Date(isoDate + 'T00:00:00')
                                            const dayNum = date.getDate()
                                            const inMonth = date.getMonth() === selectedMonth.getMonth()

                                            return (
                                                <div
                                                    key={idx}
                                                    className={`h-10 w-10 rounded-md flex flex-col items-center justify-center transition-all hover:scale-105 cursor-pointer group/cell relative ${!inMonth ? 'opacity-25' : ''}`}
                                                    style={{
                                                        background: getCellBg(count, maxCount, dm),
                                                        border: `1px solid ${getCellBorder(count, maxCount, dm)}`,
                                                    }}
                                                    title={formatTooltip(count, isoDate)}
                                                >
                                                    <span className={`text-xs font-medium ${dm ? "text-gray-300" : "text-gray-600"}`}>{dayNum}</span>
                                                    {count > 0 && (
                                                        <span className="text-[10px] font-bold" style={{ color: getCellFg(count, maxCount, dm) }}>
                                                            {count}
                                                        </span>
                                                    )}
                                                </div>
                                            )
                                        })}
                                    </div>
                                </div>
                            </motion.div>
                        ))}

                        {/* Legend */}
                        <div className={`pt-4 border-t ${dm ? "border-white/[0.08]" : "border-gray-200"} flex items-center gap-5 flex-wrap`}>
                            <span className={`text-xs font-semibold uppercase tracking-wide ${dm ? "text-gray-500" : "text-gray-700"}`}>Intensity</span>
                            {[
                                { label: 'None', count: 0, max: 10 },
                                { label: 'Low', count: 2, max: 10 },
                                { label: 'Medium', count: 5, max: 10 },
                                { label: 'High', count: 8, max: 10 },
                                { label: 'Peak', count: 10, max: 10 },
                            ].map(({ label, count, max }) => (
                                <div key={label} className="flex items-center gap-1.5">
                                    <div className="w-5 h-5 rounded" style={{ background: getCellBg(count, max, dm), border: `1px solid ${getCellBorder(count, max, dm)}` }}></div>
                                    <span className={`text-xs ${dm ? "text-gray-400" : "text-gray-700"}`}>{label}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
