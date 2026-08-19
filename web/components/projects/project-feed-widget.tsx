"use client"

import { useState } from "react"
import { motion } from "framer-motion"
import { Lightbulb, ClipboardList, CheckSquare } from "lucide-react"

export interface PMAlertItem {
  id: string
  taskKey?: string | null
  title: string
  reason: string
  recommendation: string
  severity: "critical" | "at_risk" | "info"
  bucket: "active" | "up_next"
  category: "best_practice" | "work_item"
  confidence?: number
  why?: string[]
  assignee_suggestion?: string | null
}

interface ProjectFeedWidgetProps {
  alerts: PMAlertItem[]
  actionItemOpenCount?: number
  isLoading?: boolean
  isDarkMode?: boolean
  onOpenWorkItems?: () => void
  onOpenBestPractice?: () => void
  onOpenActionItems?: () => void
}


interface StatCardProps {
  label: string
  count: number
  icon: React.ReactNode
  color: string
  delay: number
  isDarkMode: boolean
  onClick?: () => void
}

function StatCard({ label, count, icon, color, delay, isDarkMode: dm, onClick }: StatCardProps) {
  const [hovered, setHovered] = useState(false)

  return (
    <motion.button
      type="button"
      onClick={onClick}
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay, ease: [0.4, 0, 0.2, 1] }}
      whileHover={{ y: -3, transition: { duration: 0.2 } }}
      onHoverStart={() => setHovered(true)}
      onHoverEnd={() => setHovered(false)}
      className="relative overflow-hidden rounded-xl border-2 p-4 flex flex-col items-start gap-3 text-left w-full cursor-pointer"
      style={{
        borderTopColor: dm ? (hovered ? color : `${color}40`) : 'rgba(0,0,0,0.18)',
        borderRightColor: dm ? (hovered ? color : `${color}40`) : 'rgba(0,0,0,0.18)',
        borderBottomColor: dm ? (hovered ? color : `${color}40`) : 'rgba(0,0,0,0.18)',
        borderLeftColor: color,
        borderLeftWidth: dm ? '2px' : '4px',
        background: dm
          ? `linear-gradient(135deg, ${color}18, ${color}08)`
          : (hovered ? `${color}08` : 'white'),
        boxShadow: dm
          ? (hovered ? `0 6px 24px ${color}30` : `0 1px 6px ${color}10`)
          : (hovered ? `0 4px 16px ${color}25, 0 1px 4px rgba(0,0,0,0.06)` : '0 1px 4px rgba(0,0,0,0.06)'),
        transition: 'box-shadow 0.2s, background 0.2s',
      }}
    >
      {/* Icon */}
      <motion.div
        className="p-2 rounded-lg"
        style={{ backgroundColor: `${color}22`, border: `1px solid ${color}45` }}
        animate={{ boxShadow: hovered ? `0 0 18px ${color}60` : `0 0 0px transparent` }}
        transition={{ duration: 0.3 }}
      >
        <span style={{ color }}>{icon}</span>
      </motion.div>

      {/* Count */}
      <motion.span
        className="text-4xl font-black tabular-nums"
        style={{ color }}
        animate={{ scale: hovered ? 1.04 : 1 }}
        transition={{ duration: 0.2 }}
      >
        {count}
      </motion.span>

      {/* Label */}
      <span className={`text-xs font-bold uppercase tracking-wider ${dm ? 'text-gray-300' : 'text-gray-600'}`}>
        {label}
      </span>

      {/* Hover bottom sweep bar */}
      <motion.div
        className="absolute bottom-0 left-0 right-0 h-[2px]"
        style={{ background: `linear-gradient(90deg, ${color}, ${color}50)`, transformOrigin: 'left' }}
        initial={{ scaleX: 0 }}
        animate={{ scaleX: hovered ? 1 : 0 }}
        transition={{ duration: 0.25 }}
      />
    </motion.button>
  )
}

export function ProjectFeedWidget({
  alerts,
  actionItemOpenCount = 0,
  isLoading = false,
  isDarkMode = false,
  onOpenWorkItems,
  onOpenBestPractice,
  onOpenActionItems,
}: ProjectFeedWidgetProps) {
  const dm = isDarkMode
  const totalCount = alerts.length + Math.max(0, Number(actionItemOpenCount) || 0)
  const workItemCount = alerts.filter((item) => item.category === "work_item").length
  const bestPracticeCount = alerts.filter((item) => item.category === "best_practice").length
  const actionCount = Math.max(0, Number(actionItemOpenCount) || 0)


  const cardStyle = dm
    ? {
        background: 'linear-gradient(160deg, #080d1a 0%, #0d1225 50%, #080c18 100%)',
        boxShadow: '0 0 0 1px rgba(255,255,255,0.12), inset 0 1px 0 rgba(255,255,255,0.08), 0 8px 40px rgba(0,0,0,0.60)',
      }
    : {
        background: 'linear-gradient(160deg, #ffffff 0%, #f8faff 100%)',
        boxShadow: '0 0 0 1px rgba(0,0,0,0.07), inset 0 1px 0 rgba(255,255,255,1), 0 4px 16px rgba(0,0,0,0.07)',
      }
  const cardHeaderStyle = dm
    ? { background: 'linear-gradient(90deg, #060b16 0%, #0a1020 100%)' }
    : { background: 'linear-gradient(90deg, #f9fafb 0%, #f0f4ff 100%)' }

  return (
    <div className={`${dm ? "bg-[#080d1a]" : "bg-white"} rounded-xl flex flex-col overflow-hidden`} style={cardStyle}>
      {/* Header */}
      <div className={`px-5 py-4 border-b ${dm ? "border-white/[0.06]" : "border-gray-200"} flex-shrink-0`} style={cardHeaderStyle}>
        <h2 className={`text-base font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>
          Critical Alerts & Recommendations
        </h2>
      </div>

      <div className="p-4">
        {isLoading ? (
          <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>Refreshing alerts...</p>
        ) : totalCount === 0 ? (
          <p className={`text-sm italic ${dm ? "text-gray-500" : "text-gray-400"}`}>No active project alerts right now.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <StatCard
              label="Work Item Recommendations"
              count={workItemCount}
              icon={<ClipboardList className="w-4 h-4" />}
              color="#f59e0b"
              delay={0}
              isDarkMode={dm}
              onClick={onOpenWorkItems}
            />
            <StatCard
              label="Best Practice Recommendations"
              count={bestPracticeCount}
              icon={<Lightbulb className="w-4 h-4" />}
              color="#818cf8"
              delay={0.1}
              isDarkMode={dm}
              onClick={onOpenBestPractice}
            />
            <StatCard
              label="Open Action Items"
              count={actionCount}
              icon={<CheckSquare className="w-4 h-4" />}
              color="#78a530"
              delay={0.2}
              isDarkMode={dm}
              onClick={onOpenActionItems}
            />
          </div>
        )}
      </div>
    </div>
  )
}
