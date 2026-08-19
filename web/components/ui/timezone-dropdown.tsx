"use client"

import { useEffect, useMemo, useRef, useState } from "react"

type TimezoneDropdownProps = {
  value: string
  options: string[]
  onChange: (timezone: string) => void
  isDarkMode?: boolean
  placeholder?: string
  className?: string
}

type TimezoneOptionMeta = {
  value: string
  normalized: string
  abbr: string
  gmt: string
  offsetMinutes: number
  label: string
  searchText: string
}

function canonicalizeTimezoneId(timezone: string): string {
  const candidate = String(timezone || "").trim()
  if (!candidate) return ""
  try {
    const resolved = new Intl.DateTimeFormat("en-US", { timeZone: candidate }).resolvedOptions().timeZone
    return String(resolved || candidate).trim()
  } catch {
    return candidate
  }
}

function isPreferredTimezoneId(timezone: string): boolean {
  const value = String(timezone || "").trim()
  if (!value) return false
  if (value === "UTC") return true
  if (value.startsWith("Etc/")) return false
  return true
}

function normalizeGmtOffset(raw: string): string {
  const cleaned = String(raw || "").trim().toUpperCase()
  if (!cleaned || cleaned === "GMT" || cleaned === "UTC" || cleaned === "UT" || cleaned === "Z") {
    return "GMT+00:00"
  }
  const match = cleaned.match(/^GMT([+-])(\d{1,2})(?::?(\d{2}))?$/)
  if (!match) return cleaned || "GMT+00:00"
  const sign = match[1]
  const hours = String(Number(match[2] || "0")).padStart(2, "0")
  const minutes = String(Number(match[3] || "0")).padStart(2, "0")
  return `GMT${sign}${hours}:${minutes}`
}

function gmtOffsetToMinutes(gmt: string): number {
  const match = String(gmt || "").trim().toUpperCase().match(/^GMT([+-])(\d{2}):(\d{2})$/)
  if (!match) return 0
  const sign = match[1] === "-" ? -1 : 1
  const hours = Number(match[2] || "0")
  const minutes = Number(match[3] || "0")
  return sign * (hours * 60 + minutes)
}

function offsetMinutesToUtcAbbr(offsetMinutes: number): string {
  const sign = offsetMinutes < 0 ? "-" : "+"
  const absolute = Math.abs(offsetMinutes)
  const hours = Math.floor(absolute / 60)
  const minutes = absolute % 60
  if (minutes === 0) {
    return `UTC${sign}${String(hours).padStart(2, "0")}`
  }
  return `UTC${sign}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`
}

function formatTimezoneLabel(tz: string): string {
  return String(tz || "").replace(/_/g, " ")
}

function buildTimezoneMeta(timezone: string): TimezoneOptionMeta {
  const now = new Date()
  const value = String(timezone || "").trim()
  const normalized = formatTimezoneLabel(value)

  let shortAbbr = ""
  let gmtOffset = "GMT+00:00"

  try {
    const abbrParts = new Intl.DateTimeFormat("en-US", {
      timeZone: value,
      timeZoneName: "short",
      hour: "2-digit",
      minute: "2-digit",
    }).formatToParts(now)
    shortAbbr = String(abbrParts.find((part) => part.type === "timeZoneName")?.value || "").trim().toUpperCase()
  } catch {
    shortAbbr = ""
  }

  try {
    const offsetParts = new Intl.DateTimeFormat("en-US", {
      timeZone: value,
      timeZoneName: "shortOffset",
      hour: "2-digit",
      minute: "2-digit",
    }).formatToParts(now)
    const rawOffset = String(offsetParts.find((part) => part.type === "timeZoneName")?.value || "").trim().toUpperCase()
    gmtOffset = normalizeGmtOffset(rawOffset || "GMT+00:00")
  } catch {
    gmtOffset = "GMT+00:00"
  }

  const offsetMinutes = gmtOffsetToMinutes(gmtOffset)
  const abbr = shortAbbr && !shortAbbr.startsWith("GMT")
    ? shortAbbr
    : offsetMinutesToUtcAbbr(offsetMinutes)
  const label = `(${gmtOffset}) ${normalized}`
  const searchText = `${value} ${normalized} ${abbr} ${gmtOffset}`.toLowerCase()

  return { value, normalized, abbr, gmt: gmtOffset, offsetMinutes, label, searchText }
}

export function TimezoneDropdown({
  value,
  options,
  onChange,
  isDarkMode = false,
  placeholder = "Search timezone...",
  className = "",
}: TimezoneDropdownProps) {
  const dm = isDarkMode
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const rootRef = useRef<HTMLDivElement>(null)

  const optionMeta = useMemo(() => {
    const list = (Array.isArray(options) && options.length > 0 ? options : [value || "UTC"])
      .map((item) => String(item || "").trim())
      .filter(Boolean)
    const canonicalized = list
      .map((item) => canonicalizeTimezoneId(item))
      .filter(Boolean)
    const uniqueCanonical = Array.from(new Set(canonicalized))

    const supportedSet = (() => {
      try {
        const values = typeof Intl.supportedValuesOf === "function"
          ? Intl.supportedValuesOf("timeZone")
          : []
        return new Set(["UTC", ...values].map((item) => canonicalizeTimezoneId(String(item || ""))))
      } catch {
        return null
      }
    })()

    const cleaned = uniqueCanonical.filter((item) => {
      if (!isPreferredTimezoneId(item)) return false
      if (!supportedSet) return true
      return supportedSet.has(item)
    })

    return (cleaned.length > 0 ? cleaned : uniqueCanonical)
      .map(buildTimezoneMeta)
      .sort((a, b) => {
        if (a.offsetMinutes !== b.offsetMinutes) return a.offsetMinutes - b.offsetMinutes
        return a.normalized.localeCompare(b.normalized)
      })
  }, [options, value])

  const selectedMeta = useMemo(() => {
    return optionMeta.find((item) => item.value === value) || buildTimezoneMeta(value || "UTC")
  }, [optionMeta, value])

  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    if (!normalizedQuery) return optionMeta
    return optionMeta.filter((item) => item.searchText.includes(normalizedQuery))
  }, [optionMeta, query])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current) return
      if (!rootRef.current.contains(event.target as Node)) {
        setOpen(false)
        setQuery("")
      }
    }
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false)
        setQuery("")
      }
    }
    document.addEventListener("mousedown", onPointerDown)
    document.addEventListener("keydown", onEscape)
    return () => {
      document.removeEventListener("mousedown", onPointerDown)
      document.removeEventListener("keydown", onEscape)
    }
  }, [open])

  return (
    <div ref={rootRef} className={`relative w-full ${className}`}>
      <button
        type="button"
        onClick={() => {
          setOpen((prev) => !prev)
          if (open) setQuery("")
        }}
        className={`w-full rounded-xl border pl-4 pr-11 py-3 text-sm text-left focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent ${
          dm ? "border-white/[0.12] bg-[#1e2638] text-gray-100" : "border-gray-300 bg-white text-gray-900"
        }`}
      >
        <span className="block truncate">{selectedMeta.label}</span>
      </button>

      <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3">
        <svg
          className={`h-5 w-5 transition-transform ${open ? "rotate-180" : ""} ${dm ? "text-gray-400" : "text-gray-500"}`}
          viewBox="0 0 20 20"
          fill="currentColor"
        >
          <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
        </svg>
      </div>

      {open && (
        <div
          className={`absolute z-30 mt-2 w-full rounded-xl border shadow-lg ${
            dm ? "border-white/[0.12] bg-[#1e2638]" : "border-gray-200 bg-white"
          }`}
        >
          <div className={`sticky top-0 border-b p-3 ${dm ? "bg-[#1e2638] border-white/[0.08]" : "bg-white border-gray-100"}`}>
            <input
              type="text"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={placeholder}
              className={`w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent ${
                dm ? "border-white/[0.12] bg-[#161c2d] text-gray-100 placeholder-gray-500" : "border-gray-300 bg-white text-gray-900"
              }`}
            />
          </div>

          <div className="max-h-64 overflow-y-auto">
            {filtered.length > 0 ? (
              filtered.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  onClick={() => {
                    onChange(item.value)
                    setOpen(false)
                    setQuery("")
                  }}
                  className={`w-full text-left px-4 py-2.5 text-sm transition ${
                    value === item.value
                      ? dm
                        ? "bg-[#78a530]/20 text-[#78a530] font-medium"
                        : "bg-[#e9efff] text-[#1856dd] font-medium"
                      : dm
                        ? "text-gray-200 hover:bg-white/[0.06]"
                        : "text-gray-900 hover:bg-gray-50"
                  }`}
                  title={item.label}
                >
                  <span className="block truncate">{item.label}</span>
                </button>
              ))
            ) : (
              <p className={`px-4 py-3 text-sm ${dm ? "text-gray-400" : "text-gray-500"}`}>No timezone found</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
