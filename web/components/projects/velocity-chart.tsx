"use client"

import { useEffect, useRef } from "react"

interface VelocityChartProps {
    data: {
        sprint_data: Array<{
            sprint_number: number
            tasks_completed: number
            start_date: string
            end_date: string
        }>
        average_velocity: number
        trend: string
        trend_percentage: number
        predicted_next_sprint: number
    } | null
    isDarkMode?: boolean
}

export function VelocityChart({ data, isDarkMode = false }: VelocityChartProps) {
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

        const maxTasks = Math.max(...data.sprint_data.map(d => d.tasks_completed), data.predicted_next_sprint)
        const sprints = data.sprint_data.length

        const axisColor = dm ? "rgba(255,255,255,0.15)" : "#e5e7eb"
        const gridColor = dm ? "rgba(255,255,255,0.06)" : "#f3f4f6"
        const labelColor = dm ? "#94a3b8" : "#6b7280"
        const valueLabelColor = dm ? "#e2e8f0" : "#374151"

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

        // Draw bars
        const barWidth = width / (sprints + 1) * 0.6
        const barSpacing = width / (sprints + 1)

        data.sprint_data.forEach((sprint, index) => {
            const x = padding + barSpacing * (index + 0.5) - barWidth / 2
            const barHeight = (sprint.tasks_completed / maxTasks) * height
            const y = padding + height - barHeight

            ctx.fillStyle = "#78a530"
            ctx.fillRect(x, y, barWidth, barHeight)

            ctx.fillStyle = valueLabelColor
            ctx.font = "bold 12px sans-serif"
            ctx.textAlign = "center"
            ctx.fillText(sprint.tasks_completed.toString(), x + barWidth / 2, y - 5)
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
        data.sprint_data.forEach((sprint, index) => {
            const x = padding + barSpacing * (index + 0.5)
            ctx.fillText(`S${sprint.sprint_number}`, x, padding + height + 20)
        })
    }, [data, isDarkMode])

    if (!data) {
        return (
            <div className={`rounded-xl border shadow-sm p-6 ${dm ? "bg-[#1e2638] border-white/[0.08]" : "bg-white border-gray-300"}`}>
                <h3 className={`text-lg font-semibold mb-4 ${dm ? "text-gray-100" : "text-gray-900"}`}>Velocity Trend</h3>
                <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>No data available</p>
            </div>
        )
    }

    const getTrendIcon = () => {
        if (data.trend === 'improving') return '↗️'
        if (data.trend === 'declining') return '↘️'
        if (data.trend === 'stable') return '→'
        return '📊'
    }

    const getTrendColor = () => {
        if (data.trend === 'improving') return 'text-green-500'
        if (data.trend === 'declining') return 'text-red-500'
        return dm ? 'text-gray-400' : 'text-gray-600'
    }

    const getTrendText = () => {
        if (data.trend === 'improving') return `Improving (+${data.trend_percentage}%)`
        if (data.trend === 'declining') return `Declining (${data.trend_percentage}%)`
        if (data.trend === 'stable') return 'Stable'
        return 'Insufficient Data'
    }

    return (
        <div className={`rounded-xl border shadow-sm p-6 ${dm ? "bg-[#1e2638] border-white/[0.08]" : "bg-white border-gray-300"}`}>
            <div className="flex justify-between items-start mb-4">
                <div>
                    <h3 className={`text-lg font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>Velocity Trend</h3>
                    <p className={`text-sm mt-1 ${dm ? "text-gray-400" : "text-gray-500"}`}>Last 5 sprints (2-week periods)</p>
                </div>
                <div className="text-right">
                    <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg ${dm ? "bg-white/[0.08]" : "bg-gray-100"} ${getTrendColor()}`}>
                        <span className="text-lg">{getTrendIcon()}</span>
                        <span className="text-sm font-medium">{getTrendText()}</span>
                    </div>
                </div>
            </div>

            <canvas ref={canvasRef} width={600} height={300} className="w-full" />

            <div className={`grid grid-cols-2 md:grid-cols-3 gap-4 mt-6 pt-4 border-t ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
                <div>
                    <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>Average Velocity</p>
                    <p className={`text-lg font-semibold ${dm ? "text-gray-100" : "text-gray-900"}`}>{data.average_velocity} tasks/sprint</p>
                </div>
                <div>
                    <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>Trend</p>
                    <p className={`text-lg font-semibold ${getTrendColor()}`}>
                        {data.trend.charAt(0).toUpperCase() + data.trend.slice(1).replace('_', ' ')}
                    </p>
                </div>
                <div>
                    <p className={`text-xs ${dm ? "text-gray-400" : "text-gray-500"}`}>Next Sprint Forecast</p>
                    <p className="text-lg font-semibold text-[#78a530]">{data.predicted_next_sprint} tasks</p>
                </div>
            </div>
        </div>
    )
}
