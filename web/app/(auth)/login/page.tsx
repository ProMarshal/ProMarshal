"use client"

import { Suspense, useEffect } from "react"
import { useRouter, useSearchParams } from "next/navigation"

function LoginRedirect() {
  const router = useRouter()
  const searchParams = useSearchParams()

  useEffect(() => {
    // Redirect to signup page (unified auth page) with callback URL
    const callbackUrl = searchParams.get("callbackUrl")
    const signupUrl = callbackUrl
      ? `/signup?callbackUrl=${encodeURIComponent(callbackUrl)}`
      : "/signup"

    router.replace(signupUrl)
  }, [router, searchParams])

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-[#78a530]" />
    </div>
  )
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-[#78a530]" />
        </div>
      }
    >
      <LoginRedirect />
    </Suspense>
  )
}
