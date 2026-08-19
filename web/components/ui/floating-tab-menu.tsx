"use client"

import type { ReactNode } from "react"

interface FloatingTabMenuProps {
    children: ReactNode
    rightActions?: ReactNode
    className?: string
    containerClassName?: string
    isDarkMode?: boolean
}

interface FloatingTabButtonProps {
    active?: boolean
    onClick?: () => void
    className?: string
    children: ReactNode
    isDarkMode?: boolean
}

export function FloatingTabMenu({
    children,
    rightActions,
    className = "",
    containerClassName = "mb-6 flex items-center justify-between gap-3",
    isDarkMode = false,
}: FloatingTabMenuProps) {
    const dm = isDarkMode
    return (
        <div className={containerClassName}>
            <div
                className={`rounded-xl border p-1.5 inline-flex gap-1 flex-shrink-0 self-start ${className}`}
                style={dm
                    ? { background: 'linear-gradient(160deg, #080d1a 0%, #0d1225 100%)', boxShadow: '0 0 0 1px rgba(255,255,255,0.10), inset 0 1px 0 rgba(255,255,255,0.06), 0 4px 16px rgba(0,0,0,0.50)', borderColor: 'transparent' }
                    : { background: '#ffffff', boxShadow: '0 0 0 1px rgba(0,0,0,0.07), 0 2px 8px rgba(0,0,0,0.06)', borderColor: 'transparent' }
                }
            >
                {children}
            </div>
            {rightActions}
        </div>
    )
}

export function FloatingTabButton({
    active = false,
    onClick,
    className = "",
    children,
    isDarkMode = false,
}: FloatingTabButtonProps) {
    const dm = isDarkMode
    return (
        <button
            onClick={onClick}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition ${
                active
                    ? "bg-[#78a530] text-white shadow-sm"
                    : dm
                        ? "text-gray-400 hover:bg-white/[0.07] hover:text-gray-100"
                        : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
            } ${className}`}
        >
            {children}
        </button>
    )
}
