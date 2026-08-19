"use client"

import { useEffect, useState } from "react"
import Image from "next/image"

interface CompletionScreenProps {
  onProceed: () => void
  projectName?: string
}

export function CompletionScreen({ onProceed, projectName = "" }: CompletionScreenProps) {
  const [isDarkMode, setIsDarkMode] = useState(true)

  useEffect(() => {
    const saved = localStorage.getItem("promarshal_dark_mode")
    if (saved !== null) setIsDarkMode(saved === "true")
  }, [])

  const dm = isDarkMode
  const normalizedProjectName = projectName.trim()
  const successMessage = normalizedProjectName
    ? `Your project "${normalizedProjectName}" is created successfully.`
    : "Your project is created successfully."
  const completionCardStyle = dm
    ? {
        background: "linear-gradient(160deg, #081229 0%, #0a1632 45%, #071126 100%)",
        border: "1px solid rgba(120, 154, 220, 0.22)",
        boxShadow:
          "0 0 0 1px rgba(255,255,255,0.04) inset, 0 1px 0 rgba(255,255,255,0.08) inset, 0 24px 60px rgba(0,0,0,0.55)",
        backdropFilter: "blur(8px)",
      }
    : undefined

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#111520]">
      <div
        className={`rounded-2xl shadow-2xl w-full max-w-md mx-4 p-10 flex flex-col items-center text-center ${dm ? "border border-white/[0.08]" : "bg-white border border-gray-200"}`}
        style={completionCardStyle}
      >
        {/* Logo */}
        <div className="mb-6">
          <Image
            src={dm ? "/logos/logo-white.svg" : "/logos/logo-black.svg"}
            alt="ProMarshal"
            width={48}
            height={48}
            className="object-contain"
          />
        </div>

        <p className={`text-base font-semibold leading-relaxed mb-4 ${dm ? "text-gray-300" : "text-gray-600"}`}>
          {successMessage}
        </p>

        <p className={`text-xl font-semibold mb-8 ${dm ? "text-gray-100" : "text-gray-800"}`}>
          Let us complete the project setup.
        </p>

        <button
          type="button"
          onClick={onProceed}
          className="rounded-full px-10 py-3 text-base font-semibold text-white transition hover:-translate-y-0.5 hover:shadow-xl"
          style={{ backgroundColor: "#78a530" }}
        >
          Let's go
        </button>
      </div>
    </div>
  )
}
