"use client"

import { useState, useRef, useEffect } from "react"
import Image from "next/image"
import { TimezoneDropdown } from "@/components/ui/timezone-dropdown"

interface ProjectSetupFormProps {
  onComplete: (data: {
    projectName: string
    projectType: string
    tier: string
    timezone: string
    inviteEmail: string
  }) => void
  isSubmitting?: boolean
}

const projectTypes = [
  "Software / Product Development",
  "Implementation",
]

export function ProjectSetupForm({ onComplete, isSubmitting = false }: ProjectSetupFormProps) {
  const [projectName, setProjectName] = useState("")
  const [projectType, setProjectType] = useState("Software / Product Development")
  const tier = "paid"
  const [timezone, setTimezone] = useState("UTC")
  const [timezones, setTimezones] = useState<string[]>([])
  const [inviteEmail, setInviteEmail] = useState("")
  const [error, setError] = useState("")
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [isDarkMode, setIsDarkMode] = useState(true)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const saved = localStorage.getItem("promarshal_dark_mode")
    if (saved !== null) setIsDarkMode(saved === "true")
  }, [])

  const dm = isDarkMode

  // Close dropdowns when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  useEffect(() => {
    const fetchTimezones = async () => {
      try {
        const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
        const response = await fetch(`${backendUrl}/api/projects/timezones`, { cache: "no-store" })
        if (!response.ok) {
          throw new Error(`timezone_fetch_failed status=${response.status}`)
        }
        const data = await response.json()
        if (Array.isArray(data) && data.length > 0) {
          const normalized = data.filter((tz) => typeof tz === "string")
          setTimezones(normalized)
          const browserTimezone = String(Intl.DateTimeFormat().resolvedOptions().timeZone || "").trim()
          if (browserTimezone && normalized.includes(browserTimezone)) {
            setTimezone(browserTimezone)
          } else if (normalized.includes("Asia/Kolkata")) {
            setTimezone("Asia/Kolkata")
          } else if (normalized.includes("UTC")) {
            setTimezone("UTC")
          } else {
            setTimezone(normalized[0])
          }
        }
      } catch (error) {
        console.error("Failed to fetch global timezone list:", error)
      }
    }
    fetchTimezones()
  }, [])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError("")

    if (!projectName.trim()) {
      setError("Project name is required")
      return
    }

    if (projectName.trim().length < 5) {
      setError("Project name must be at least 5 characters")
      return
    }

    if (!projectType) {
      setError("Please select a project type")
      return
    }

    if (inviteEmail.trim()) {
      const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
      if (!emailPattern.test(inviteEmail.trim())) {
        setError("Please enter a valid email address")
        return
      }
    }

    onComplete({
      projectName: projectName.trim(),
      projectType,
      tier,
      timezone,
      inviteEmail: inviteEmail.trim(),
    })
  }

  const inputCls = `w-full rounded-xl border px-5 py-4 text-base focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent ${
    dm ? "border-white/[0.12] bg-[#1e2638] text-gray-100 placeholder-gray-500" : "border-gray-300 bg-white text-gray-900"
  }`

  const labelCls = `block text-base font-medium ${dm ? "text-gray-200" : "text-gray-900"}`

  return (
    <div className="h-screen overflow-y-auto bg-[#111520]">
      <div className="w-full max-w-2xl mx-auto px-6 py-12 pb-24">
        {/* Logo */}
        <div className="mb-12 flex justify-center">
          <div className="w-16 h-16">
            <Image
              src={dm ? "/logos/logo-white.svg" : "/logos/logo-black.svg"}
              alt="ProMarshal"
              width={64}
              height={64}
              className="w-full h-full object-contain"
            />
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-12">
          {/* Project Name */}
          <div className="space-y-3">
            <label htmlFor="project-name" className={labelCls}>
              Name your project space <span className="text-red-500">*</span>
            </label>
            <input
              id="project-name"
              type="text"
              value={projectName}
              onChange={(e) => {
                setProjectName(e.target.value)
                setError("")
              }}
              placeholder="Whatever comes to your mind first is always the best..."
              className={inputCls}
              autoFocus
            />
          </div>

          {/* Project Type */}
          <div className="space-y-3" ref={dropdownRef}>
            <label htmlFor="project-type" className={labelCls}>
              What type of project do you manage?
            </label>
            <div className="relative">
              <button
                type="button"
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className={`w-full rounded-xl border pl-5 pr-12 py-4 text-base text-left focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent ${
                  dm ? "border-white/[0.12] bg-[#1e2638] text-gray-100" : "border-gray-300 bg-white text-gray-900"
                }`}
              >
                {projectType}
              </button>
              <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-4">
                <svg
                  className={`h-5 w-5 transition-transform ${isDropdownOpen ? "rotate-180" : ""} ${dm ? "text-gray-400" : "text-gray-500"}`}
                  viewBox="0 0 20 20"
                  fill="currentColor"
                >
                  <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </div>

              {isDropdownOpen && (
                <div className={`absolute z-10 mt-2 w-full rounded-xl border shadow-lg max-h-64 overflow-auto ${
                  dm ? "border-white/[0.12] bg-[#1e2638]" : "border-gray-200 bg-white"
                }`}>
                  {projectTypes.map((type) => (
                    <button
                      key={type}
                      type="button"
                      onClick={() => {
                        setProjectType(type)
                        setIsDropdownOpen(false)
                      }}
                      className={`w-full text-left px-5 py-3 text-base transition ${
                        projectType === type
                          ? dm ? "bg-[#78a530]/20 text-[#78a530] font-medium" : "bg-[#e9efff] text-[#1856dd] font-medium"
                          : dm ? "text-gray-200 hover:bg-white/[0.06]" : "text-gray-900 hover:bg-gray-50"
                      }`}
                    >
                      {type}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Timezone Selection */}
          <div className="space-y-3">
            <label htmlFor="timezone" className={labelCls}>
              Select Timezone <span className="text-red-500">*</span>
            </label>
            <TimezoneDropdown
              value={timezone}
              options={timezones.length > 0 ? timezones : [timezone]}
              onChange={(nextTimezone) => setTimezone(nextTimezone)}
              isDarkMode={dm}
              className="max-w-xl"
            />
          </div>

          {/* Invite Team */}
          <div className="space-y-3">
            <label htmlFor="invite-email" className={labelCls}>
              Invite your team
            </label>
            <input
              id="invite-email"
              type="email"
              value={inviteEmail}
              onChange={(e) => {
                setInviteEmail(e.target.value)
                setError("")
              }}
              placeholder="name@domain.com"
              className={inputCls}
            />
            <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>
              You can invite your team anytime later as well.
            </p>
          </div>

          {/* Error Message */}
          {error && (
            <div className={`rounded-xl p-4 ${dm ? "bg-red-900/30 border border-red-800/50" : "bg-red-50"}`}>
              <p className={`text-sm font-medium ${dm ? "text-red-400" : "text-red-600"}`}>{error}</p>
            </div>
          )}

          {/* Submit Button */}
          <div className="flex justify-end pt-4">
            <button
              type="submit"
              disabled={isSubmitting}
              className="rounded-full px-10 py-4 text-base font-semibold text-white transition hover:-translate-y-0.5 hover:shadow-xl disabled:opacity-60 disabled:cursor-not-allowed"
              style={{ backgroundColor: "#78a530" }}
            >
              {isSubmitting ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
