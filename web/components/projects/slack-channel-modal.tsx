"use client"

import { useState, useEffect, useRef } from "react"
import { X, ChevronDown, Search, Check } from "lucide-react"
import { Loading } from "@/components/ui/loading"

interface SlackChannelModalProps {
  isOpen: boolean
  onClose: () => void
  projectId: string
  userId: string
  backendToken?: string
  onSuccess?: () => void
  isDarkMode?: boolean
}

export function SlackChannelModal({
  isOpen,
  onClose,
  projectId,
  userId,
  backendToken,
  onSuccess,
  isDarkMode = false,
}: SlackChannelModalProps) {
  const dm = isDarkMode
  const [isLoading, setIsLoading] = useState(true)
  const [availableChannels, setAvailableChannels] = useState<any[]>([])
  const [selectedChannelIds, setSelectedChannelIds] = useState<string[]>([])
  const [searchQuery, setSearchQuery] = useState("")
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [checkingChannelId, setCheckingChannelId] = useState<string | null>(null)

  const [conflictInfo, setConflictInfo] = useState<{
    channelName: string
    projectName: string
  } | null>(null)

  const [showInviteModal, setShowInviteModal] = useState(false)
  const [savedChannelNames, setSavedChannelNames] = useState<string[]>([])

  const dropdownRef = useRef<HTMLDivElement>(null)
  const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"

  const glassStyle = dm ? {
    background: "linear-gradient(135deg, rgba(30,38,56,0.95) 0%, rgba(17,21,32,0.98) 100%)",
    backdropFilter: "blur(20px)",
    WebkitBackdropFilter: "blur(20px)",
    border: "1px solid rgba(255,255,255,0.18)",
    boxShadow: "0 8px 40px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.12)",
  } : {}

  const fetchChannels = async () => {
    if (!projectId || !backendToken) return
    setIsLoading(true)
    try {
      const [channelsRes, selectedRes] = await Promise.all([
        fetch(`${backendUrl}/api/integrations/slack/channels/${projectId}`, {
          headers: { Authorization: `Bearer ${backendToken}` },
        }),
        fetch(`${backendUrl}/api/integrations/slack/selected-channels/${projectId}`, {
          headers: { Authorization: `Bearer ${backendToken}` },
        })
      ])
      if (channelsRes.ok) {
        const data = await channelsRes.json()
        setAvailableChannels(data.channels || [])
      }
      if (selectedRes.ok) {
        const data = await selectedRes.json()
        setSelectedChannelIds((data.selected_channels || []).map((ch: any) => ch.channel_id))
      }
    } catch (error) {
      console.error("Error fetching channels:", error)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    if (isOpen) {
      setShowInviteModal(false)
      setSavedChannelNames([])
      setConflictInfo(null)
      setDropdownOpen(false)
      setSearchQuery("")
      fetchChannels()
    }
  }, [isOpen, projectId, userId, backendToken])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  const handleSelectChannel = async (channel: any) => {
    const channelId = channel.id
    if (selectedChannelIds.includes(channelId)) {
      setSelectedChannelIds([])
      setDropdownOpen(false)
      return
    }
    setCheckingChannelId(channelId)
    try {
      const res = await fetch(
        `${backendUrl}/api/integrations/slack/check-channel/${projectId}?channel_id=${encodeURIComponent(channelId)}`,
        {
          headers: backendToken ? { Authorization: `Bearer ${backendToken}` } : {},
        },
      )
      if (res.ok) {
        const data = await res.json()
        if (data.conflict) {
          setConflictInfo({ channelName: channel.name, projectName: data.project_name })
          setDropdownOpen(false)
          return
        }
      }
    } catch {
      // Silent fail
    } finally {
      setCheckingChannelId(null)
    }
    setSelectedChannelIds([channelId])
    setDropdownOpen(false)
  }

  const handleSave = async () => {
    if (!projectId || !backendToken) return
    setIsSaving(true)
    try {
      const queryParams = selectedChannelIds.map(id => `channel_ids=${encodeURIComponent(id)}`).join("&")
      const response = await fetch(
        `${backendUrl}/api/integrations/slack/channels/${projectId}?${queryParams}`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${backendToken}` },
        }
      )
      if (response.ok) {
        const names = availableChannels
          .filter(ch => selectedChannelIds.includes(ch.id))
          .map(ch => ch.name)
        setSavedChannelNames(names)
        onSuccess?.()
        setShowInviteModal(true)
      } else {
        const data = await response.json().catch(() => ({}))
        console.error("Failed to save channels:", data.detail)
      }
    } catch (error) {
      console.error("Error saving channels:", error)
    } finally {
      setIsSaving(false)
    }
  }

  const handleSkip = async () => {
    if (!projectId || !backendToken) return
    setIsSaving(true)
    try {
      await fetch(
        `${backendUrl}/api/integrations/slack/channels/${projectId}`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${backendToken}` },
        }
      )
      onSuccess?.()
      onClose()
    } catch (error) {
      console.error("Error skipping:", error)
    } finally {
      setIsSaving(false)
    }
  }

  const selectedChannels = availableChannels.filter(ch => selectedChannelIds.includes(ch.id))
  const filteredChannels = availableChannels.filter(ch =>
    ch.name.toLowerCase().includes(searchQuery.toLowerCase())
  )

  if (!isOpen) return null

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/60 z-40" onClick={onClose} />

      {/* Main modal */}
      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div
          className={`rounded-2xl shadow-2xl max-w-lg w-full pointer-events-auto ${dm ? "" : "bg-white border-2 border-gray-300"}`}
          style={dm ? glassStyle : {}}
        >
          {/* Header */}
          <div className={`flex items-center justify-between p-6 border-b ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
            <h2 className={`text-xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>Select Slack Channel</h2>
            <button
              onClick={onClose}
              className={`p-2 rounded-lg transition ${dm ? "hover:bg-white/[0.08] text-gray-400" : "hover:bg-gray-100 text-gray-500"}`}
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Content */}
          <div className="p-6">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loading size="lg" message="Loading Slack channels..." />
              </div>
            ) : availableChannels.length === 0 ? (
              <div className="text-center py-8">
                <div className="text-5xl mb-4">📢</div>
                <h3 className={`text-lg font-bold mb-2 ${dm ? "text-gray-100" : "text-gray-900"}`}>No Channels Found</h3>
                <p className={`text-sm ${dm ? "text-gray-400" : "text-gray-600"}`}>
                  Create a channel in Slack first, then come back here.
                </p>
              </div>
            ) : (
              <>
                <p className={`text-sm mb-4 ${dm ? "text-gray-400" : "text-gray-600"}`}>
                  Choose the channel(s) where ProMarshal should send task reminders and updates.
                </p>

                {/* Dropdown */}
                <div className="relative" ref={dropdownRef}>
                  <button
                    type="button"
                    onClick={() => setDropdownOpen(prev => !prev)}
                    className={`w-full flex items-center justify-between px-4 py-3 border-2 rounded-xl text-left hover:border-[#78a530] focus:outline-none focus:border-[#78a530] transition ${dm ? "border-white/[0.12] bg-[#111520] text-gray-100" : "border-gray-300 bg-white"}`}
                  >
                    <span className={selectedChannels.length === 0 ? (dm ? "text-gray-500" : "text-gray-400") : (dm ? "text-gray-100 font-medium" : "text-gray-900 font-medium")}>
                      {selectedChannels.length === 0
                        ? "Select a channel..."
                        : selectedChannels.map(ch => `#${ch.name}`).join(", ")}
                    </span>
                    <ChevronDown className={`w-4 h-4 flex-shrink-0 transition-transform ${dropdownOpen ? "rotate-180" : ""} ${dm ? "text-gray-400" : "text-gray-400"}`} />
                  </button>

                  {dropdownOpen && (
                    <div
                      className={`absolute top-full left-0 right-0 mt-1 rounded-xl shadow-xl z-10 overflow-hidden ${dm ? "border border-white/[0.10]" : "border border-gray-200 bg-white"}`}
                      style={dm ? {
                        background: "linear-gradient(135deg, rgba(30,38,56,0.98) 0%, rgba(17,21,32,0.99) 100%)",
                        backdropFilter: "blur(20px)",
                        WebkitBackdropFilter: "blur(20px)",
                      } : {}}
                    >
                      {/* Search */}
                      <div className={`p-2 border-b ${dm ? "border-white/[0.08]" : "border-gray-100"}`}>
                        <div className={`flex items-center gap-2 px-3 py-2 rounded-lg ${dm ? "bg-[#0d1117]" : "bg-gray-50"}`}>
                          <Search className={`w-4 h-4 flex-shrink-0 ${dm ? "text-gray-500" : "text-gray-400"}`} />
                          <input
                            type="text"
                            placeholder="Search channels..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            className={`flex-1 bg-transparent text-sm focus:outline-none ${dm ? "text-gray-200 placeholder-gray-600" : "text-gray-700 placeholder-gray-400"}`}
                            autoFocus
                          />
                        </div>
                      </div>

                      {/* Channel options */}
                      <div className="max-h-56 overflow-y-auto py-1">
                        {filteredChannels.length === 0 ? (
                          <p className={`text-sm text-center py-4 ${dm ? "text-gray-500" : "text-gray-400"}`}>No channels match</p>
                        ) : (
                          filteredChannels.map((channel) => {
                            const isSelected = selectedChannelIds.includes(channel.id)
                            const isChecking = checkingChannelId === channel.id
                            return (
                              <button
                                key={channel.id}
                                type="button"
                                disabled={isChecking}
                                onClick={() => handleSelectChannel(channel)}
                                className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition disabled:opacity-60 ${
                                  isSelected
                                    ? dm ? "bg-[#78a530]/10" : "bg-[#78a530]/5"
                                    : dm ? "hover:bg-white/[0.05]" : "hover:bg-gray-50"
                                }`}
                              >
                                <div className={`w-4 h-4 rounded flex-shrink-0 border-2 flex items-center justify-center transition ${
                                  isSelected ? "bg-[#78a530] border-[#78a530]" : dm ? "border-white/[0.25]" : "border-gray-300"
                                }`}>
                                  {isSelected && <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />}
                                </div>
                                <div className="flex-1 min-w-0">
                                  <span className={`text-sm font-medium ${dm ? "text-gray-200" : "text-gray-900"}`}>#{channel.name}</span>
                                  <span className={`text-xs ml-2 ${dm ? "text-gray-500" : "text-gray-400"}`}>{channel.num_members} members</span>
                                </div>
                                {isChecking && <span className={`text-xs ${dm ? "text-gray-500" : "text-gray-400"}`}>Checking...</span>}
                              </button>
                            )
                          })
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* Clear selection */}
                {selectedChannels.length > 0 && (
                  <div className="flex items-center justify-end mt-2">
                    <button
                      type="button"
                      onClick={() => setSelectedChannelIds([])}
                      className={`text-xs transition ${dm ? "text-gray-500 hover:text-gray-300" : "text-gray-400 hover:text-gray-600"}`}
                    >
                      Clear selection
                    </button>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Footer */}
          <div className="flex gap-3 px-6 pb-6">
            <button
              onClick={handleSkip}
              disabled={isSaving}
              className={`flex-1 px-6 py-3 border-2 rounded-xl font-semibold transition disabled:opacity-50 ${dm ? "border-white/[0.12] text-gray-300 hover:bg-white/[0.06]" : "border-gray-300 text-gray-700 hover:bg-gray-100"}`}
            >
              Skip for Now
            </button>
            <button
              onClick={handleSave}
              disabled={selectedChannelIds.length === 0 || isSaving}
              className="flex-1 px-6 py-3 bg-[#78a530] text-white rounded-xl font-semibold hover:bg-[#6a9129] transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSaving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </div>

      {/* Conflict modal */}
      {conflictInfo && (
        <div className="fixed inset-0 flex items-center justify-center z-[70] p-4 pointer-events-none">
          <div
            className={`rounded-2xl shadow-2xl max-w-sm w-full p-8 pointer-events-auto ${dm ? "" : "bg-white border-2 border-orange-300"}`}
            style={dm ? { ...glassStyle, border: "1px solid rgba(251,146,60,0.25)" } : {}}
          >
            <div className="text-center">
              <div className={`mb-4 mx-auto flex h-14 w-14 items-center justify-center rounded-full ${dm ? "bg-orange-900/40" : "bg-orange-100"}`}>
                <svg className={`h-7 w-7 ${dm ? "text-orange-400" : "text-orange-500"}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                </svg>
              </div>
              <h2 className={`text-xl font-bold mb-2 ${dm ? "text-gray-100" : "text-gray-900"}`}>Channel Already in Use</h2>
              <p className={`mb-2 ${dm ? "text-gray-400" : "text-gray-600"}`}>
                <span className={`font-semibold ${dm ? "text-gray-200" : "text-gray-900"}`}>#{conflictInfo.channelName}</span> is already linked to another project:
              </p>
              <p className={`text-sm font-semibold rounded-lg px-3 py-2 mb-6 ${dm ? "text-orange-400 bg-orange-900/30" : "text-orange-700 bg-orange-50"}`}>
                {conflictInfo.projectName}
              </p>
              <p className={`text-sm mb-6 ${dm ? "text-gray-500" : "text-gray-500"}`}>Please select a different channel.</p>
              <button
                onClick={() => setConflictInfo(null)}
                className="w-full px-6 py-3 bg-[#78a530] text-white rounded-xl font-semibold hover:bg-[#6a9129] transition"
              >
                Choose Another Channel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Invite bot modal */}
      {showInviteModal && (
        <div className="fixed inset-0 flex items-center justify-center z-[70] p-4 pointer-events-none">
          <div
            className={`rounded-2xl shadow-2xl max-w-sm w-full p-8 pointer-events-auto ${dm ? "" : "bg-white border-2 border-[#78a530]/40"}`}
            style={dm ? { ...glassStyle, border: "1px solid rgba(120,165,48,0.25)" } : {}}
          >
            <div className="text-center">
              <div className={`mb-4 mx-auto flex h-14 w-14 items-center justify-center rounded-full ${dm ? "bg-[#78a530]/20" : "bg-[#78a530]/10"}`}>
                <svg className="h-7 w-7 text-[#78a530]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h2 className={`text-xl font-bold mb-2 ${dm ? "text-gray-100" : "text-gray-900"}`}>Channel Saved!</h2>
              <p className={`mb-4 ${dm ? "text-gray-400" : "text-gray-600"}`}>
                One last step — invite ProMarshal to your channel so it can send reminders and updates.
              </p>

              <div className={`rounded-xl p-4 mb-6 text-left border ${dm ? "bg-[#0d1117] border-white/[0.08]" : "bg-gray-50 border-gray-200"}`}>
                <p className={`text-xs font-semibold uppercase tracking-wide mb-2 ${dm ? "text-gray-500" : "text-gray-500"}`}>In Slack, type:</p>
                <div className="flex items-center gap-2">
                  <code className={`text-sm font-mono rounded px-2 py-1 flex-1 border ${dm ? "text-gray-200 bg-[#1e2638] border-white/[0.08]" : "text-gray-900 bg-white border-gray-200"}`}>
                    /invite @ProMarshal
                  </code>
                  <button
                    onClick={() => navigator.clipboard.writeText("/invite @ProMarshal")}
                    className={`p-1.5 transition ${dm ? "text-gray-500 hover:text-gray-300" : "text-gray-400 hover:text-gray-600"}`}
                    title="Copy"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  </button>
                </div>
                {savedChannelNames.length > 0 && (
                  <p className={`text-xs mt-2 ${dm ? "text-gray-500" : "text-gray-500"}`}>
                    in {savedChannelNames.map(n => `#${n}`).join(", ")}
                  </p>
                )}
              </div>

              <button
                onClick={() => {
                  setShowInviteModal(false)
                  onClose()
                }}
                className="w-full px-6 py-3 bg-[#78a530] text-white rounded-xl font-semibold hover:bg-[#6a9129] transition"
              >
                Got it!
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
