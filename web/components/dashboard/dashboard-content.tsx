"use client"

import { useEffect, useState } from "react"
import { WelcomeScreen } from "@/components/onboarding/welcome-screen"
import { ProjectSetupForm } from "@/components/onboarding/project-setup-form"
import { CompletionScreen } from "@/components/onboarding/completion-screen"
import { Project } from "@/lib/api"

interface DashboardContentProps {
  initialProjects: Project[]
  userId: string
  userName: string
  forceSetup?: boolean
}

type OnboardingStep = "welcome" | "setup" | "completion" | null

export function DashboardContent({ initialProjects, userId, userName, forceSetup = false }: DashboardContentProps) {
  const [projects, setProjects] = useState<Project[]>(initialProjects)
  // Initialize currentStep based on initial conditions
  const [currentStep, setCurrentStep] = useState<OnboardingStep>(() => {
    if (initialProjects.length === 0 || forceSetup) {
      // Skip welcome screen if user already has projects (forced setup)
      return forceSetup && initialProjects.length > 0 ? "setup" : "welcome"
    }
    return null
  })
  const [isCreating, setIsCreating] = useState(false)
  const [newlyCreatedProject, setNewlyCreatedProject] = useState<Project | null>(null)

  const handleWelcomeContinue = () => {
    setCurrentStep("setup")
  }

  const handleProjectSetupComplete = async (data: {
    projectName: string
    projectType: string
    tier: string
    timezone: string
    inviteEmail: string
  }) => {
    setIsCreating(true)
    try {
      console.log("Creating project with data:", data)
      const response = await fetch("/api/projects/create", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          projectName: data.projectName,
          userId: userId,
          projectType: data.projectType,
          tier: data.tier,
          timezone: data.timezone,
          invitedEmails: data.inviteEmail ? [data.inviteEmail] : [],
        }),
      })

      console.log("Response status:", response.status)
      
      if (response.ok) {
        const newProject = await response.json()
        console.log("Project created successfully:", newProject)
        setProjects((prev) => [...prev, newProject])
        setNewlyCreatedProject(newProject)
        setCurrentStep("completion")
      } else {
        const errorData = await response.json().catch(() => ({}))
        console.error("Failed to create project:", response.status, errorData)
        alert(`Failed to create project: ${errorData.error || response.statusText}`)
      }
    } catch (error) {
      console.error("Error creating project:", error)
      alert("An error occurred. Please try again.")
    } finally {
      setIsCreating(false)
    }
  }

  const handleCompletionProceed = () => {
    // Redirect to the newly created project (PM Board view)
    if (newlyCreatedProject?._id) {
      window.location.href = `/projects?projectId=${newlyCreatedProject._id}`
    } else {
      // Fallback: redirect without params (will show latest project)
      window.location.href = "/projects"
    }
  }

  // Render appropriate screen based on current step
  if (currentStep === "welcome") {
    return <WelcomeScreen userName={userName} onContinue={handleWelcomeContinue} />
  }

  if (currentStep === "setup") {
    return (
      <ProjectSetupForm
        onComplete={handleProjectSetupComplete}
        isSubmitting={isCreating}
      />
    )
  }

  if (currentStep === "completion") {
    return (
      <CompletionScreen
        onProceed={handleCompletionProceed}
        projectName={newlyCreatedProject?.project_name || ""}
      />
    )
  }

  return null
}
