"use client"

import { Suspense, useEffect, useRef, useState } from "react"
import { useSession } from "next-auth/react"
import { useRouter, useSearchParams } from "next/navigation"

type Status = "idle" | "approving" | "success" | "error" | "unauthenticated"

export default function ConnectPluginPage() {
  // useSearchParams() forces this subtree out of static prerendering, so we
  // wrap it in Suspense to satisfy Next.js App Router's prerender contract.
  return (
    <Suspense fallback={
      <div style={styles.page}>
        <div style={styles.card}>
          <p style={styles.muted}>Loading...</p>
        </div>
      </div>
    }>
      <ConnectPluginContent />
    </Suspense>
  )
}

function ConnectPluginContent() {
  const { data: session, status: authStatus } = useSession()
  const searchParams = useSearchParams()
  const router = useRouter()

  const [status, setStatus] = useState<Status>("idle")
  const [errorMsg, setErrorMsg] = useState("")
  const sentRef = useRef(false)

  // Extension ID passed as ?ext=<id> by the plugin
  const extensionId = searchParams.get("ext") || ""

  useEffect(() => {
    if (authStatus === "unauthenticated") {
      router.push(`/login?callbackUrl=${encodeURIComponent(window.location.href)}`)
    }
  }, [authStatus, router])

  // Auto-approve as soon as the user is authenticated and an extension ID is present.
  // This is the seamless flow used when the extension itself opens this tab on install.
  useEffect(() => {
    if (authStatus !== "authenticated") return
    if (!extensionId) return
    if (sentRef.current) return
    handleApprove()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authStatus, extensionId])

  async function handleApprove() {
    if (sentRef.current) return
    sentRef.current = true
    setStatus("approving")

    try {
      const res = await fetch("/api/plugin/token", {
        method: "POST",
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `Server error: ${res.status}`)
      }

      const { token, user_id, name } = await res.json()

      // Send token to the extension
      if (extensionId && typeof window !== "undefined" && (window as any).chrome?.runtime?.sendMessage) {
        ;(window as any).chrome.runtime.sendMessage(
          extensionId,
          {
            type: "AUTH_TOKEN",
            token,
            user_id,
            name,
          },
          (response: any) => {
            if ((window as any).chrome.runtime.lastError) {
              console.error("Extension message error:", (window as any).chrome.runtime.lastError)
            }
          }
        )
      }

      setStatus("success")

      // Auto-close the tab after a short success-state display
      setTimeout(() => {
        try { window.close() } catch { /* ignore — close blocked, user can close manually */ }
      }, 1800)
    } catch (err: any) {
      sentRef.current = false
      setErrorMsg(err.message || "Something went wrong")
      setStatus("error")
    }
  }

  if (authStatus === "loading") {
    return (
      <div style={styles.page}>
        <div style={styles.card}>
          <p style={styles.muted}>Loading...</p>
        </div>
      </div>
    )
  }

  if (authStatus === "unauthenticated") {
    return null
  }

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        {/* Logo mark */}
        <div style={styles.logoMark}>PM</div>

        {status === "success" ? (
          <>
            <h1 style={styles.heading}>Connected</h1>
            <p style={styles.body}>
              ProMarshal plugin is now connected to your account
              <strong> {session?.user?.email}</strong>.
              You can close this tab.
            </p>
            <div style={styles.successIcon}>&#10003;</div>
          </>
        ) : status === "approving" ? (
          <>
            <h1 style={styles.heading}>Connecting...</h1>
            <p style={styles.body}>
              Linking the ProMarshal plugin to
              <strong> {session?.user?.email}</strong>.
            </p>
            <div style={styles.spinner} />
          </>
        ) : (
          <>
            <h1 style={styles.heading}>Connect Plugin</h1>
            <p style={styles.body}>
              The ProMarshal Chrome extension wants to connect to your account
              <strong> {session?.user?.email}</strong>.
            </p>
            <p style={styles.body}>
              This allows the plugin to capture meeting audio, transcribe it, and store
              insights in your projects.
            </p>

            {status === "error" && (
              <p style={styles.error}>{errorMsg}</p>
            )}

            <button
              onClick={handleApprove}
              style={styles.button}
            >
              Approve Connection
            </button>

            <p style={styles.muted}>
              You can revoke this at any time from plugin settings.
            </p>
          </>
        )}
      </div>
      <style>{`
        @keyframes pm-spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    background: "#f9fafb",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "24px",
  },
  card: {
    background: "#fff",
    borderRadius: "12px",
    border: "1px solid #e5e7eb",
    padding: "48px 40px",
    maxWidth: "440px",
    width: "100%",
    textAlign: "center",
    boxShadow: "0 4px 24px rgba(0,0,0,0.06)",
  },
  logoMark: {
    width: "56px",
    height: "56px",
    borderRadius: "14px",
    background: "#78a530",
    color: "#fff",
    fontSize: "20px",
    fontWeight: 700,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    margin: "0 auto 24px",
  },
  heading: {
    fontSize: "22px",
    fontWeight: 700,
    color: "#111827",
    margin: "0 0 16px",
  },
  body: {
    fontSize: "15px",
    color: "#374151",
    lineHeight: 1.6,
    margin: "0 0 16px",
  },
  button: {
    display: "block",
    width: "100%",
    padding: "12px 24px",
    background: "#78a530",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    fontSize: "15px",
    fontWeight: 600,
    cursor: "pointer",
    margin: "24px 0 16px",
  },
  buttonDisabled: {
    opacity: 0.6,
    cursor: "not-allowed",
  },
  muted: {
    fontSize: "13px",
    color: "#9ca3af",
    margin: "0",
  },
  error: {
    fontSize: "14px",
    color: "#dc2626",
    background: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: "6px",
    padding: "10px 14px",
    margin: "0 0 8px",
  },
  successIcon: {
    fontSize: "48px",
    color: "#78a530",
    margin: "16px 0 0",
  },
  spinner: {
    width: "36px",
    height: "36px",
    border: "3px solid #e5e7eb",
    borderTopColor: "#78a530",
    borderRadius: "50%",
    margin: "24px auto 0",
    animation: "pm-spin 0.8s linear infinite",
  },
}
