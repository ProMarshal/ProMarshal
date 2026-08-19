"use client"

import { useState, useEffect, Suspense } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { useSession } from "next-auth/react"
import Image from "next/image"

function SlackSetupContent() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const { data: session } = useSession()

  const projectId = searchParams.get("project_id")
  const backendToken = String(session?.user?.backendToken || "").trim()

  const [isLoading, setIsLoading] = useState(true)
  const [availableChannels, setAvailableChannels] = useState<any[]>([])
  const [selectedChannelIds, setSelectedChannelIds] = useState<string[]>([])
  const [searchQuery, setSearchQuery] = useState("")
  const [isSaving, setIsSaving] = useState(false)
  const [showBotWarning, setShowBotWarning] = useState(false)
  const [channelsNeedingBot, setChannelsNeedingBot] = useState<string[]>([])

  const fetchChannels = async () => {
    if (!projectId || !backendToken) return
    setIsLoading(true)

    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"

      const channelsResponse = await fetch(
        `${backendUrl}/api/integrations/slack/channels/${projectId}`,
        { headers: { Authorization: `Bearer ${backendToken}` } }
      )

      if (channelsResponse.ok) {
        const channelsData = await channelsResponse.json()
        setAvailableChannels(channelsData.channels || [])
      }

      const selectedResponse = await fetch(
        `${backendUrl}/api/integrations/slack/selected-channels/${projectId}`,
        { headers: { Authorization: `Bearer ${backendToken}` } }
      )

      if (selectedResponse.ok) {
        const selectedData = await selectedResponse.json()
        const currentlySelected = selectedData.selected_channels || []
        setSelectedChannelIds(currentlySelected.map((ch: any) => ch.channel_id))
      }
    } catch (error) {
      console.error("Error fetching channels:", error)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchChannels()
  }, [projectId, backendToken])

  const toggleChannelSelection = (channelId: string) => {
    setSelectedChannelIds(prev =>
      prev.includes(channelId)
        ? prev.filter(id => id !== channelId)
        : [...prev, channelId]
    )
  }

  const selectAllChannels = () => {
    setSelectedChannelIds(availableChannels.map(ch => ch.id))
  }

  const deselectAllChannels = () => {
    setSelectedChannelIds([])
  }

  const handleSave = async () => {
    if (!projectId || !backendToken) return

    // Check if any selected channels don't have the bot
    const channelsWithoutBot = availableChannels
      .filter(ch => selectedChannelIds.includes(ch.id) && !ch.is_member)
      .map(ch => ch.name)

    if (channelsWithoutBot.length > 0) {
      setChannelsNeedingBot(channelsWithoutBot)
      setShowBotWarning(true)
      return
    }

    setIsSaving(true)

    try {
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"
      const queryParams = selectedChannelIds.map(id => `channel_ids=${encodeURIComponent(id)}`).join('&')

      const response = await fetch(
        `${backendUrl}/api/integrations/slack/channels/${projectId}?${queryParams}`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${backendToken}` },
        }
      )

      if (response.ok) {
        router.push("/projects?nav=Control Panel&tab=Integrate Tools")
      } else {
        console.error("Failed to save channels")
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
      const backendUrl = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"

      // Save with empty channel_ids to mark as skipped
      const response = await fetch(
        `${backendUrl}/api/integrations/slack/channels/${projectId}`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${backendToken}` },
        }
      )

      if (response.ok) {
        // Redirect to projects page with Integrate Tools tab active
        router.push("/projects?nav=Control Panel&tab=Integrate Tools")
      } else {
        console.error("Failed to skip channel selection")
      }
    } catch (error) {
      console.error("Error skipping:", error)
    } finally {
      setIsSaving(false)
    }
  }

  const filteredChannels = availableChannels.filter(ch =>
    ch.name.toLowerCase().includes(searchQuery.toLowerCase())
  )

  if (!projectId) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-gray-600">Missing project ID</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 py-12 px-4">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="flex justify-center mb-4">
            <Image src="/logos/slack.png" alt="Slack" width={64} height={64} />
          </div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            Select Channels to Monitor
          </h1>
          <p className="text-gray-600">
            Choose which Slack channels ProMarshal should read messages from
          </p>
        </div>

        {/* Content Card */}
        <div className="bg-white rounded-2xl shadow-lg p-8">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-[#78a530]"></div>
            </div>
          ) : availableChannels.length === 0 ? (
            /* Empty State */
            <div className="text-center py-16">
              <div className="text-6xl mb-4">📢</div>
              <h2 className="text-2xl font-bold text-gray-900 mb-4">
                No Channels Found
              </h2>
              <p className="text-gray-600 mb-8 max-w-md mx-auto">
                Your Slack workspace doesn't have any channels yet. Please create a channel in Slack first, then come back to configure ProMarshal.
              </p>
              <button
                onClick={handleSkip}
                disabled={isSaving}
                className="px-8 py-3 bg-gray-200 text-gray-700 rounded-lg font-semibold hover:bg-gray-300 transition disabled:opacity-50"
              >
                {isSaving ? "Saving..." : "Skip for Now"}
              </button>
              <p className="text-sm text-gray-500 mt-4">
                You can configure channels later from the Projects page
              </p>
            </div>
          ) : (
            /* Channel Selection */
            <>
              {/* Search and Controls */}
              <div className="mb-6 space-y-4">
                <input
                  type="text"
                  placeholder="Search channels..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#78a530] focus:border-transparent"
                />
                <div className="flex items-center justify-between">
                  <div className="flex gap-3">
                    <button
                      onClick={selectAllChannels}
                      className="px-4 py-2 text-sm text-[#78a530] border border-[#78a530] rounded-lg hover:bg-[#78a530] hover:text-white transition"
                    >
                      Select All
                    </button>
                    <button
                      onClick={deselectAllChannels}
                      className="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-100 transition"
                    >
                      Deselect All
                    </button>
                  </div>
                  <span className="text-sm text-gray-600">
                    {selectedChannelIds.length} of {availableChannels.length} selected
                  </span>
                </div>
              </div>

              {/* Channel List */}
              <div className="space-y-2 mb-8 max-h-96 overflow-y-auto">
                {filteredChannels.map((channel) => (
                  <label
                    key={channel.id}
                    className="flex items-center gap-4 p-4 border border-gray-200 rounded-lg hover:bg-gray-50 cursor-pointer transition"
                  >
                    <input
                      type="checkbox"
                      checked={selectedChannelIds.includes(channel.id)}
                      onChange={() => toggleChannelSelection(channel.id)}
                      className="w-5 h-5 text-[#78a530] border-gray-300 rounded focus:ring-[#78a530]"
                    />
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-gray-900">#{channel.name}</span>
                        {channel.is_member && (
                          <span className="text-xs text-[#78a530] bg-[#78a530]/10 px-2 py-1 rounded">
                            ✓ Bot is member
                          </span>
                        )}
                        {!channel.is_member && (
                          <span className="text-xs text-orange-600 bg-orange-50 px-2 py-1 rounded">
                            ⚠️ Bot not invited
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-gray-500">{channel.num_members} members</p>
                    </div>
                  </label>
                ))}
              </div>

              {/* Action Buttons */}
              <div className="flex gap-4">
                <button
                  onClick={handleSkip}
                  disabled={isSaving}
                  className="flex-1 px-6 py-3 border-2 border-gray-300 text-gray-700 rounded-lg font-semibold hover:bg-gray-100 transition disabled:opacity-50"
                >
                  Skip for Now
                </button>
                <button
                  onClick={handleSave}
                  disabled={selectedChannelIds.length === 0 || isSaving}
                  className="flex-1 px-6 py-3 bg-[#78a530] text-white rounded-lg font-semibold hover:bg-[#6a9129] transition disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isSaving ? "Saving..." : `Save Selection (${selectedChannelIds.length})`}
                </button>
              </div>
            </>
          )}
        </div>

        {/* Bot Invitation Warning Modal */}
        {showBotWarning && (
          <>
            <div className="fixed inset-0 bg-black bg-opacity-50 z-40" onClick={() => setShowBotWarning(false)} />
            <div className="fixed inset-0 flex items-center justify-center z-50 p-4">
              <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8">
                <div className="text-center">
                  <div className="text-5xl mb-4">⚠️</div>
                  <h2 className="text-2xl font-bold text-gray-900 mb-4">Invite Bot to Channels</h2>
                  <p className="text-gray-600 mb-4">
                    Please invite the ProMarshal bot to these channels first:
                  </p>
                  <div className="bg-orange-50 border border-orange-200 rounded-lg p-4 mb-6">
                    {channelsNeedingBot.map((name, idx) => (
                      <div key={idx} className="text-orange-800 font-semibold">#{name}</div>
                    ))}
                  </div>
                  <button
                    onClick={() => {
                      setShowBotWarning(false)
                      fetchChannels()
                    }}
                    className="w-full px-6 py-3 bg-[#78a530] text-white rounded-lg font-semibold hover:bg-[#6a9129] transition"
                  >
                    Got it
                  </button>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default function SlackSetupPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center">
          <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-[#78a530]" />
        </div>
      }
    >
      <SlackSetupContent />
    </Suspense>
  )
}
