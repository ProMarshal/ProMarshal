"use client"

import { useState } from "react"

interface TooltipProps {
  content: string
  children: React.ReactNode
  className?: string
}

export function Tooltip({ content, children, className = "" }: TooltipProps) {
  const [show, setShow] = useState(false)

  return (
    <div
      className={`relative inline-flex ${className}`}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && content && (
        <div className="absolute z-[9999] px-2 py-1 text-xs text-white bg-gray-900 rounded whitespace-nowrap pointer-events-none right-full mr-2 top-1/2 -translate-y-1/2">
          {content}
          {/* Arrow pointing right */}
          <div className="absolute left-full top-1/2 -translate-y-1/2 -ml-px">
            <div className="border-4 border-transparent border-l-gray-900" />
          </div>
        </div>
      )}
    </div>
  )
}
