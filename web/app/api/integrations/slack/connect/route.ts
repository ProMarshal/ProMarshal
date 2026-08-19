import { NextRequest, NextResponse } from "next/server"
import { auth } from "@/lib/auth"

const BACKEND_URL =
  process.env.PYTHON_API_URL ||
  process.env.NEXT_PUBLIC_PYTHON_API_URL ||
  "http://localhost:8000"

export async function GET(request: NextRequest) {
  const session = await auth()
  const backendToken = String(session?.user?.backendToken || "").trim()
  if (!backendToken) {
    return NextResponse.redirect(new URL("/login", request.url), { status: 302 })
  }

  const incoming = new URL(request.url)
  const projectId = String(incoming.searchParams.get("project_id") || "").trim()
  const returnNav = String(incoming.searchParams.get("return_nav") || "").trim()
  const returnTab = String(incoming.searchParams.get("return_tab") || "").trim()
  if (!projectId) {
    return NextResponse.json({ error: "project_id is required" }, { status: 400 })
  }

  const params = new URLSearchParams({ project_id: projectId })
  if (returnNav) params.set("return_nav", returnNav)
  if (returnTab) params.set("return_tab", returnTab)

  const upstreamResponse = await fetch(
    `${BACKEND_URL}/api/integrations/slack/connect?${params.toString()}`,
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${backendToken}`,
      },
      cache: "no-store",
      redirect: "manual",
    },
  )

  const location = upstreamResponse.headers.get("location")
  if (location) {
    return NextResponse.redirect(location, { status: 302 })
  }
  if (upstreamResponse.redirected && upstreamResponse.url) {
    return NextResponse.redirect(upstreamResponse.url, { status: 302 })
  }
  const body = await upstreamResponse.text().catch(() => "")
  return NextResponse.json(
    {
      error: "Failed to start Slack OAuth flow",
      status: upstreamResponse.status,
      detail: body,
    },
    { status: 502 },
  )
}
