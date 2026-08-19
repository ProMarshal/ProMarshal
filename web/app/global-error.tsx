"use client"

import * as Sentry from "@sentry/nextjs"
import { useEffect } from "react"

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    Sentry.captureException(error)
  }, [error])

  return (
    <html>
      <body>
        <div className="flex min-h-screen flex-col items-center justify-center bg-gray-50">
          <div className="text-center space-y-4">
            <h2 className="text-2xl font-bold text-gray-900">Something went wrong</h2>
            <p className="text-gray-600">We have been notified and are looking into it.</p>
            <button
              onClick={reset}
              className="rounded-full bg-[#78a530] px-6 py-2 text-sm font-semibold text-white hover:bg-[#6a9329]"
            >
              Try again
            </button>
          </div>
        </div>
      </body>
    </html>
  )
}
