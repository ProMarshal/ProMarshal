import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000"

export async function POST() {
  const session = await auth()

  if (!session?.user) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 })
  }

  const backendToken = (session.user as any).backendToken
  if (!backendToken) {
    return NextResponse.json({ detail: "Backend token missing from session" }, { status: 401 })
  }

  const res = await fetch(`${PYTHON_API_URL}/api/integrations/plugin/token`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${backendToken}`,
      "Content-Type": "application/json",
    },
  })

  const data = await res.json().catch(() => ({}))

  if (!res.ok) {
    return NextResponse.json(data, { status: res.status })
  }

  return NextResponse.json(data)
}
