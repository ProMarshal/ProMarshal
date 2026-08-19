"use client"

import { useState, useEffect } from "react"
import { signIn } from "next-auth/react"
import { OTPInput } from "./otp-input"

const PYTHON_API_URL = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://localhost:8000"

interface EmailOTPFormProps {
  callbackUrl?: string
}

export function EmailOTPForm({ callbackUrl = "/dashboard" }: EmailOTPFormProps) {
  const [step, setStep] = useState<"email" | "otp">("email")
  const [email, setEmail] = useState("")
  const [otp, setOtp] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resendTimer, setResendTimer] = useState(0)

  // Countdown timer for resend
  useEffect(() => {
    if (resendTimer > 0) {
      const timer = setTimeout(() => setResendTimer(resendTimer - 1), 1000)
      return () => clearTimeout(timer)
    }
  }, [resendTimer])

  const handleSendOTP = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    
    if (!email || !email.includes("@")) {
      setError("Please enter a valid email address")
      return
    }

    setLoading(true)
    setError(null)

    try {
      const response = await fetch(`${PYTHON_API_URL}/api/auth/send-otp`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email })
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || "Failed to send OTP")
      }

      setStep("otp")
      setResendTimer(45) // 45 seconds before allowing resend
    } catch (err: any) {
      setError(err.message || "Failed to send OTP. Please try again.")
    } finally {
      setLoading(false)
    }
  }

  const handleVerifyOTP = async (otpCode: string) => {
    setLoading(true)
    setError(null)

    try {
      // Use NextAuth's signIn with credentials provider
      const result = await signIn("email-otp", {
        email: email,
        otp: otpCode,
        redirect: false,
      })

      if (result?.error) {
        throw new Error("Invalid OTP. Please try again.")
      }

      if (result?.ok) {
        // Redirect to callback URL
        window.location.href = callbackUrl
      }
    } catch (err: any) {
      setError(err.message || "Verification failed. Please try again.")
      setLoading(false)
    }
  }

  const handleResend = () => {
    if (resendTimer === 0) {
      handleSendOTP()
    }
  }

  const handleBack = () => {
    setStep("email")
    setOtp("")
    setError(null)
  }

  if (step === "email") {
    return (
      <div className="space-y-4">
        <form onSubmit={handleSendOTP} className="space-y-4">
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-2">
              Email address
            </label>
            <input
              id="email"
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={loading}
              className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#78a530] focus:border-[#78a530] focus:outline-none transition disabled:bg-gray-100 disabled:cursor-not-allowed"
              required
              autoFocus
            />
          </div>

          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-sm text-red-600">{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={!email || loading}
            className="w-full bg-[#78a530] text-white font-semibold py-3 px-6 rounded-lg hover:bg-[#6a9428] transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <svg className="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Sending...
              </>
            ) : (
              "Continue with Email"
            )}
          </button>
        </form>
      </div>
    )
  }

  // OTP verification step
  return (
    <div className="space-y-6">
      <div className="text-center">
        <h3 className="text-xl font-bold text-gray-900 mb-2">Check your email</h3>
        <p className="text-sm text-gray-600 mb-1">
          We sent a 6-digit code to:
        </p>
        <p className="text-sm font-semibold text-gray-900 mb-4">{email}</p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-3 text-center">
          Enter verification code
        </label>
        <OTPInput
          length={6}
          onComplete={handleVerifyOTP}
          disabled={loading}
        />
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
          <p className="text-sm text-red-600 text-center">{error}</p>
        </div>
      )}

      {loading && (
        <div className="text-center">
          <div className="inline-flex items-center gap-2 text-sm text-gray-600">
            <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            Verifying...
          </div>
        </div>
      )}

      <div className="text-center space-y-3">
        <button
          onClick={handleResend}
          disabled={resendTimer > 0 || loading}
          className="text-sm text-gray-600 hover:text-gray-900 disabled:opacity-50 disabled:cursor-not-allowed transition"
        >
          {resendTimer > 0 ? (
            <>Resend code in {resendTimer}s</>
          ) : (
            <>Didn&apos;t receive it? <span className="font-semibold text-[#78a530]">Resend</span></>
          )}
        </button>

        <button
          onClick={handleBack}
          disabled={loading}
          className="block w-full text-sm text-gray-600 hover:text-gray-900 disabled:opacity-50 disabled:cursor-not-allowed transition"
        >
          ← Back to email
        </button>
      </div>
    </div>
  )
}

