"use client"

import { useEffect, useRef } from "react"

interface BurndownChartProps {
    data: {
        daily_data: Array<{
            date: string
            day: number
            actual_remaining: number
            ideal_remaining: number
        }>
        total_tasks: number
        completed_tasks: number
        remaining_tasks: number
        velocity: number
        predicted_completion_date: string | null
        is_on_track: boolean
        sprint_start: string
        sprint_end: string
    } | null
    isDarkMode?: boolean
}

export function BurndownChart({ data, isDarkMode = false }: BurndownChartProps) {
    const dm = isDarkMode
    const canvasRef = useRef<HTMLCanvasElement>(null)

    useEffect(() => {
        if (!data || !canvasRef.current) return

        const canvas = canvasRef.current
        const ctx = canvas.getContext("2d")
        if (!ctx) return

        ctx.clearRect(0, 0, canvas.width, canvas.height)

        const padding = 40
        const width = canvas.width - padding * 2
        const height = canvas.height - padding * 2

        const maxTasks = Math.max(...data.daily_data.map(d => Math.max(d.actual_remaining, d.ideal_remaining)))
        const days = data.daily_data.length

        const axisColor = dm ? "rgba(255,255,255,0.15)" : "#e5e7eb"
        const gridColor = dm ? "rgba(255,255,255,0.06)" : "#f3f4f6"
        const labelColor = dm ? "#94a3b8" : "#6b7280"

        // Draw axes
        ctx.strokeStyle = axisColor
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(padding, padding)
        ctx.lineTo(padding, padding + height)
        ctx.lineTo(padding + width, padding + height)
        ctx.stroke()

        // Draw grid lines
        ctx.strokeStyle = gridColor
        for (let i = 0; i <= 5; i++) {
            const y = padding + (height / 5) * i
            ctx.beginPath()
            ctx.moveTo(padding, y)
            ctx.lineTo(padding + width, y)
            ctx.stroke()
        }

        // Draw ideal line (dashed)
        ctx.strokeStyle = dm ? "#64748b" : "#9ca3af"
        ctx.lineWidth = 2
        ctx.setLineDash([5, 5])
        ctx.beginPath()
        data.daily_data.forEach((point, index) => {
            const x = padding + (width / (days - 1)) * index
            const y = padding + height - (point.ideal_remaining / maxTasks) * height
            if (index === 0) ctx.moveTo(x, y)
            else ctx.lineTo(x, y)
        })
        ctx.stroke()

        // Draw actual line (solid)
        ctx.strokeStyle = data.is_on_track ? "#78a530" : "#ef4444"
        ctx.lineWidth = 3
        ctx.setLineDash([])
        ctx.beginPath()
        data.daily_data.forEach((point, index) => {
            const x = padding + (width / (days - 1)) * index
            const y = padding + height - (point.actual_remaining / maxTasks) * height
            if (index === 0) ctx.moveTo(x, y)
            else ctx.lineTo(x, y)
        })
        ctx.stroke()

        // Draw points on actual line
        ctx.fillStyle = data.is_on_track ? "#78a530" : "#ef4444"
        data.daily_data.forEach((point, index) => {
            const x = padding + (width / (days - 1)) * index
            const y = padding + height - (point.actual_remaining / maxTasks) * height
            ctx.beginPath()
            ctx.arc(x, y, 4, 0, Math.PI * 2)
            ctx.fill()
        })

        // Draw Y-axis labels
        ctx.fillStyle = labelColor
        ctx.font = "12px sans-serif"
        ctx.textAlign = "right"
        for (let i = 0; i <= 5; i++) {
            const value = Math.round(maxTasks - (maxTasks / 5) * i)
            const y = padding + (height / 5) * i
            ctx.fillText(value.toString(), padding - 10, y + 4)
        }

        // Draw X-axis labels
        ctx.textAlign = "center"
        data.daily_data.forEach((point, index) => {
            if (index % 3 === 0 || index === days - 1) {
                const x = padding + (width / (days - 1)) * index
                ctx.fillText(`Day ${point.day}`, x, padding + height + 20)
            }
        })
    }, [data, isDarkMode])

    if (!data) {
        return (
            <div className={`rounded-xl border shadow-sm p-6 ${dm ? "bg-[#1e2638] border-white/[0.08]" : "bg-white border-gray-300"}`}>
                <h3 className={`text-lg font-semibold mb-4 ${dm ? "text-gray-100" : "text-gray-900"}`}>Sprint Burndown</h3>
                <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>No data available</p>
            </div>
        )
    }

    const completionDate = data.predicted_completion_date
        ? new Date(data.predicted_completion_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
        : 'N/A'

    return (
        <div className={`rounded-xl border shadow-sm p-6 ${dm ? "bg-[#1e2638] border-white/[0.08]" : "bg-white border-gray-300"}`}>
            <div className="flex justify-between items-start mb-4">
                <div>
                    <h3 className={`text-lg font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Sprint Burndown</h3>
                    <p className={`text-sm mt-1 ${dm ? "text-gray-400" : "text-gray-500"}`}>
                        {new Date(data.sprint_start).toLocaleDateString()} - {new Date(data.sprint_end).toLocaleDateString()}
                    </p>
                </div>
                <div className="text-right">
                    <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg ${data.is_on_track ? (dm ? 'bg-green-900/40 text-green-300' : 'bg-green-100 text-green-700') : (dm ? 'bg-red-900/40 text-red-300' : 'bg-red-100 text-red-700')}`}>
                        <span className="text-sm font-medium">{data.is_on_track ? 'On Track' : 'At Risk'}</span>
                    </div>
                </div>
            </div>

            <canvas ref={canvasRef} width={600} height={300} className="w-full" />

            <div className={`grid grid-cols-2 md:grid-cols-4 gap-4 mt-6 pt-4 border-t ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
                <div>
                    <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>Total Tasks</p>
                    <p className={`text-lg font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>{data.total_tasks}</p>
                </div>
                <div>
                    <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>Completed</p>
                    <p className="text-lg font-semibold text-green-500">{data.completed_tasks}</p>
                </div>
                <div>
                    <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>Remaining</p>
                    <p className="text-lg font-semibold text-orange-500">{data.remaining_tasks}</p>
                </div>
                <div>
                    <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>Predicted Completion</p>
                    <p className={`text-sm font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>{completionDate}</p>
                </div>
            </div>

            <div className="mt-4 flex items-center gap-4 text-sm">
                <div className="flex items-center gap-2">
                    <div className={`w-8 h-0.5 ${dm ? "bg-slate-500" : "bg-gray-400"}`} style={{ borderTop: `2px dashed ${dm ? "#64748b" : "#9ca3af"}` }}></div>
                    <span className={dm ? "text-gray-400" : "text-gray-600"}>Ideal</span>
                </div>
                <div className="flex items-center gap-2">
                    <div className={`w-8 h-1 ${data.is_on_track ? 'bg-[#78a530]' : 'bg-red-500'}`}></div>
                    <span className={dm ? "text-gray-400" : "text-gray-600"}>Actual</span>
                </div>
            </div>
        </div>
    )
}
