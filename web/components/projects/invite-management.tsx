"use client"

import { useState } from "react"
import { useSession } from "next-auth/react"
import { ProjectMember } from "@/lib/api"

interface InviteManagementProps {
  projectId: string
  userId: string
  members: ProjectMember[]
  onMembersChange: () => void
}

const PYTHON_API_URL = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"

export function InviteManagement({
  projectId,
  userId,
  members,
  onMembersChange,
}: InviteManagementProps) {
  const { data: session } = useSession()
  const [inviteEmail, setInviteEmail] = useState("")
  const [inviteRole, setInviteRole] = useState<"admin" | "member">("member")
  const [isInviting, setIsInviting] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const backendToken = String(session?.user?.backendToken || "").trim()
  const backendFetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
    const headers = new Headers(init.headers || {})
    if (backendToken && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${backendToken}`)
    }
    return window.fetch(input, { ...init, headers })
  }

  const activeMembers = members.filter((m) => m.status === "active")
  const pendingInvites = members.filter((m) => m.status === "pending")

  const handleSendInvite = async () => {
    if (!inviteEmail.trim()) {
      setError("Please enter an email address")
      return
    }

    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
    if (!emailPattern.test(inviteEmail)) {
      setError("Please enter a valid email address")
      return
    }

    setIsInviting(true)
    setError("")
    setSuccess("")

    try {
      const response = await backendFetch(`${PYTHON_API_URL}/api/projects/${projectId}/invite`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: inviteEmail,
          role: inviteRole,
          invited_by: userId,
        }),
      })

      if (response.ok) {
        setSuccess(`Invitation sent to ${inviteEmail}`)
        setInviteEmail("")
        onMembersChange()
      } else {
        const data = await response.json()
        setError(data.detail || "Failed to send invitation")
      }
    } catch (err) {
      setError("An error occurred. Please try again.")
    } finally {
      setIsInviting(false)
    }
  }

  const handleResendInvite = async (email: string) => {
    try {
      const response = await backendFetch(
        `${PYTHON_API_URL}/api/projects/${projectId}/invite/resend?email=${encodeURIComponent(email)}`,
        { method: "POST" }
      )
      if (response.ok) {
        setSuccess(`Invitation resent to ${email}`)
        onMembersChange()
      } else {
        const data = await response.json()
        setError(data.detail || "Failed to resend invitation")
      }
    } catch (err) {
      setError("Failed to resend invitation")
    }
  }

  const handleCancelInvite = async (email: string) => {
    try {
      const response = await backendFetch(
        `${PYTHON_API_URL}/api/projects/${projectId}/invite/cancel?email=${encodeURIComponent(email)}`,
        { method: "DELETE" }
      )
      if (response.ok) {
        setSuccess("Invitation cancelled")
        onMembersChange()
      } else {
        const data = await response.json()
        setError(data.detail || "Failed to cancel invitation")
      }
    } catch (err) {
      setError("Failed to cancel invitation")
    }
  }

  const handleUpdateRole = async (memberUserId: string, newRole: "admin" | "member") => {
    try {
      const response = await backendFetch(
        `${PYTHON_API_URL}/api/projects/${projectId}/members/${memberUserId}/role`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: newRole }),
        }
      )
      if (response.ok) {
        setSuccess("Role updated")
        onMembersChange()
      } else {
        const data = await response.json()
        setError(data.detail || "Failed to update role")
      }
    } catch (err) {
      setError("Failed to update role")
    }
  }

  const handleRemoveMember = async (memberUserId: string) => {
    if (!confirm("Are you sure you want to remove this member?")) return

    try {
      const response = await backendFetch(
        `${PYTHON_API_URL}/api/projects/${projectId}/members/${memberUserId}`,
        { method: "DELETE" }
      )
      if (response.ok) {
        setSuccess("Member removed")
        onMembersChange()
      } else {
        const data = await response.json()
        setError(data.detail || "Failed to remove member")
      }
    } catch (err) {
      setError("Failed to remove member")
    }
  }

  const getRoleBadgeStyle = (role: string) => {
    switch (role) {
      case "owner":
        return { backgroundColor: "rgba(120,165,48,0.12)", color: "#78a530" }
      case "admin":
        return { backgroundColor: "rgba(24,86,221,0.12)", color: "#1856dd" }
      default:
        return { backgroundColor: "rgba(156,163,175,0.2)", color: "#6b7280" }
    }
  }

  return (
    <div className="space-y-6">
      {/* Send Invite Form */}
      <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <h3 className="mb-4 text-base font-semibold text-gray-900">Invite Team Member</h3>
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            type="email"
            value={inviteEmail}
            onChange={(e) => {
              setInviteEmail(e.target.value)
              setError("")
            }}
            placeholder="Enter email address"
            className="flex-1 rounded-xl border border-gray-300 bg-white px-4 py-3 text-sm focus:border-[#1856dd] focus:outline-none focus:ring-2 focus:ring-[#1856dd]/30"
          />
          <select
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value as "admin" | "member")}
            className="rounded-xl border border-gray-300 bg-white px-4 py-3 text-sm focus:border-[#1856dd] focus:outline-none focus:ring-2 focus:ring-[#1856dd]/30"
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
          <button
            type="button"
            onClick={handleSendInvite}
            disabled={isInviting}
            className="rounded-full px-6 py-3 text-sm font-semibold text-white transition hover:-translate-y-0.5 disabled:opacity-60"
            style={{ backgroundColor: "#78a530" }}
          >
            {isInviting ? "Sending..." : "Send Invite"}
          </button>
        </div>
        {error && <p className="mt-2 text-sm font-medium text-red-600">{error}</p>}
        {success && <p className="mt-2 text-sm font-medium text-green-600">{success}</p>}
      </div>

      {/* Active Members */}
      <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-5 py-4">
          <h3 className="text-base font-semibold text-gray-900">
            Team Members ({activeMembers.length})
          </h3>
        </div>
        <div className="divide-y divide-gray-100">
          {activeMembers.map((member, index) => (
            <div key={member.user_id || index} className="flex items-center justify-between px-5 py-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gray-800 text-sm font-bold text-white">
                  {(member.name || member.email || "U").charAt(0).toUpperCase()}
                </div>
                <div>
                  <p className="text-sm font-semibold text-gray-900">
                    {member.name || member.email || "Unknown"}
                  </p>
                  {member.name && member.email && (
                    <p className="text-xs text-gray-500">{member.email}</p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span
                  className="rounded-full px-3 py-1 text-xs font-semibold"
                  style={getRoleBadgeStyle(member.role)}
                >
                  {member.role}
                </span>
                {member.role !== "owner" && member.user_id && (
                  <div className="flex items-center gap-2">
                    <select
                      value={member.role}
                      onChange={(e) => handleUpdateRole(member.user_id!, e.target.value as "admin" | "member")}
                      className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs focus:outline-none"
                    >
                      <option value="member">Member</option>
                      <option value="admin">Admin</option>
                    </select>
                    <button
                      type="button"
                      onClick={() => handleRemoveMember(member.user_id!)}
                      className="rounded-lg p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600"
                      title="Remove member"
                    >
                      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Pending Invites */}
      {pendingInvites.length > 0 && (
        <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-100 px-5 py-4">
            <h3 className="text-base font-semibold text-gray-900">
              Pending Invites ({pendingInvites.length})
            </h3>
          </div>
          <div className="divide-y divide-gray-100">
            {pendingInvites.map((invite, index) => (
              <div key={invite.email || index} className="flex items-center justify-between px-5 py-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gray-200 text-sm font-bold text-gray-500">
                    {(invite.email || "?").charAt(0).toUpperCase()}
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{invite.email}</p>
                    <p className="text-xs text-gray-500">
                      Invited {invite.invited_at ? new Date(invite.invited_at).toLocaleDateString() : "recently"}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span
                    className="rounded-full px-3 py-1 text-xs font-semibold"
                    style={{ backgroundColor: "rgba(251,191,36,0.15)", color: "#d97706" }}
                  >
                    Pending
                  </span>
                  <button
                    type="button"
                    onClick={() => handleResendInvite(invite.email!)}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium text-[#1856dd] hover:bg-[#1856dd]/5"
                  >
                    Resend
                  </button>
                  <button
                    type="button"
                    onClick={() => handleCancelInvite(invite.email!)}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
