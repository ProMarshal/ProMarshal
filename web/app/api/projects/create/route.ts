import { NextRequest, NextResponse } from "next/server"
import { createProject } from "@/lib/api"
import { auth } from "@/lib/auth"

export async function POST(request: NextRequest) {
  try {
    const session = await auth()
    const backendToken = String(session?.user?.backendToken || "").trim()
    if (!backendToken) {
      return NextResponse.json(
        { error: "Missing backend token" },
        { status: 401 }
      )
    }

    const body = await request.json()
    const { projectName, userId, teamSize, projectType, tier, timezone, invitedEmails } = body

    if (!projectName || !userId) {
      return NextResponse.json(
        { error: "Project name and user ID are required" },
        { status: 400 }
      )
    }

    const result = await createProject({
      projectName,
      userId,
      teamSize,
      projectType,
      tier,
      timezone,
      invitedEmails,
    }, backendToken)

    if (!result.ok || !result.project) {
      return NextResponse.json(
        { error: result.error || "Failed to create project" },
        { status: result.status || 500 }
      )
    }

    return NextResponse.json(result.project, { status: 201 })
  } catch (error) {
    console.error("Error in create project API:", error)
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    )
  }
}
