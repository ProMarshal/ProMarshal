"use client"

import { useEffect, useState } from "react"
import { useSession } from "next-auth/react"
import Link from "next/link"

const PYTHON_API_URL = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"

interface InviteInfo {
  valid: boolean
  project_id?: string
  project_name?: string
  invited_by_name?: string
  role?: string
  email?: string
  expired?: boolean
  already_accepted?: boolean
}

interface InviteAcceptClientProps {
  token: string
}

export function InviteAcceptClient({ token }: InviteAcceptClientProps) {
  const { data: session, status } = useSession()
  const [inviteInfo, setInviteInfo] = useState<InviteInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [processing, setProcessing] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")

  useEffect(() => {
    async function verifyInvite() {
      try {
        const response = await fetch(`${PYTHON_API_URL}/api/projects/invite/verify/${token}`)
        if (response.ok) {
          const data = await response.json()
          setInviteInfo(data)
        } else {
          setInviteInfo({ valid: false })
        }
      } catch (err) {
        setInviteInfo({ valid: false })
      } finally {
        setLoading(false)
      }
    }
    verifyInvite()
  }, [token])

  const handleAccept = async () => {
    if (!session?.user) {
      // Redirect to login with callback
      window.location.href = `/signup?callbackUrl=${encodeURIComponent(`/invite/${token}`)}`
      return
    }

    // Check if logged-in user's email matches invite email
    if (inviteInfo?.email && session.user.email?.toLowerCase() !== inviteInfo.email.toLowerCase()) {
      setError(`This invite is for ${inviteInfo.email}. Please sign in with that email address.`)
      return
    }

    setProcessing(true)
    setError("")

    try {
      const response = await fetch(`${PYTHON_API_URL}/api/projects/invite/accept/${token}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          // OLD: user_id: (session.user as any).user_id || session.user.email?.split("@")[0],
          user_id: (session.user as any).userId || session.user.email,
          name: session.user.name || "User",
          email: session.user.email,
        }),
      })

      if (response.ok) {
        const data = await response.json()
        setSuccess("Invitation accepted! Redirecting to project...")
        setTimeout(() => {
          window.location.href = `/projects?projectId=${data.project_id}`
        }, 2000)
      } else {
        const data = await response.json()
        setError(data.detail || "Failed to accept invitation")
      }
    } catch (err) {
      setError("An error occurred. Please try again.")
    } finally {
      setProcessing(false)
    }
  }

  const handleDecline = async () => {
    if (!confirm("Are you sure you want to decline this invitation?")) return

    setProcessing(true)
    setError("")

    try {
      const response = await fetch(`${PYTHON_API_URL}/api/projects/invite/decline/${token}`, {
        method: "POST",
      })

      if (response.ok) {
        setSuccess("Invitation declined.")
        setInviteInfo({ valid: false })
      } else {
        const data = await response.json()
        setError(data.detail || "Failed to decline invitation")
      }
    } catch (err) {
      setError("An error occurred. Please try again.")
    } finally {
      setProcessing(false)
    }
  }

  // Show loading while fetching invite info OR session
  if (loading || status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f5f6f8]">
        <div className="text-center">
          <div className="mb-4 h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-[#1856dd] mx-auto" />
          <p className="text-gray-600">Loading invitation...</p>
        </div>
      </div>
    )
  }

  if (!inviteInfo?.valid) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f5f6f8] p-6">
        <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-lg text-center">
          <div className="mb-6 mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-red-100">
            <svg className="h-8 w-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <h1 className="mb-2 text-2xl font-bold text-gray-900">Invalid Invitation</h1>
          <p className="mb-6 text-gray-600">
            {inviteInfo?.expired
              ? "This invitation has expired. Please ask for a new invite."
              : inviteInfo?.already_accepted
                ? "This invitation has already been accepted."
                : "This invitation link is invalid or no longer active."}
          </p>
          <Link
            href="/signup"
            className="inline-block rounded-full px-6 py-3 text-sm font-semibold text-white transition"
            style={{ backgroundColor: "#1856dd" }}
          >
            Go to Login
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f5f6f8] p-6">
      <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-lg">
        <div className="mb-6 text-center">
          <div className="mb-4 mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-[#1856dd]/10">
            <svg className="h-8 w-8 text-[#1856dd]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <h1 className="mb-2 text-2xl font-bold text-gray-900">You&apos;re Invited!</h1>
          <p className="text-gray-600">
            {inviteInfo.invited_by_name
              ? `${inviteInfo.invited_by_name} has invited you to join`
              : "You have been invited to join"}
          </p>
        </div>

        <div className="mb-6 rounded-xl bg-gray-50 p-4 text-center">
          <p className="text-lg font-bold text-gray-900">{inviteInfo.project_name || "Project"}</p>
          <p className="text-sm text-gray-600">
            as <span className="font-semibold capitalize">{inviteInfo.role || "member"}</span>
          </p>
        </div>

        <div className="mb-6 rounded-xl border border-gray-200 p-4">
          <p className="text-sm text-gray-600 mb-1">Invitation for:</p>
          <p className="text-sm font-semibold text-gray-900">{inviteInfo.email}</p>
        </div>

        {!session && (
          <div className="mb-6 rounded-xl bg-blue-50 p-4 text-center">
            <p className="text-sm text-blue-800">
              Please sign in with <strong>{inviteInfo.email}</strong> to accept this invitation.
            </p>
          </div>
        )}

        {session && session.user?.email?.toLowerCase() !== inviteInfo.email?.toLowerCase() && (
          <div className="mb-6 rounded-xl bg-yellow-50 p-4 text-center">
            <p className="text-sm text-yellow-800">
              You&apos;re signed in as <strong>{session.user?.email}</strong>.
              This invite is for <strong>{inviteInfo.email}</strong>.
            </p>
          </div>
        )}

        {error && (
          <div className="mb-4 rounded-xl bg-red-50 p-3 text-center">
            <p className="text-sm font-medium text-red-600">{error}</p>
          </div>
        )}

        {success && (
          <div className="mb-4 rounded-xl bg-green-50 p-3 text-center">
            <p className="text-sm font-medium text-green-600">{success}</p>
          </div>
        )}

        <div className="flex flex-col gap-3">
          <button
            type="button"
            onClick={handleAccept}
            disabled={processing}
            className="w-full rounded-full py-3 text-sm font-semibold text-white transition hover:-translate-y-0.5 disabled:opacity-60"
            style={{ backgroundColor: "#78a530" }}
          >
            {processing ? "Processing..." : session ? "Accept Invitation" : "Sign In to Accept"}
          </button>
          <button
            type="button"
            onClick={handleDecline}
            disabled={processing}
            className="w-full rounded-full border border-gray-300 py-3 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
          >
            Decline
          </button>
        </div>
      </div>
    </div>
  )
}
