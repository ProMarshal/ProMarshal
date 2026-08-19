"use client"

import React from "react"
import BugIcon from "@atlaskit/icon/core/bug"
import EpicIcon from "@atlaskit/icon/core/epic"
import StoryIcon from "@atlaskit/icon/core/story"
import SubtasksIcon from "@atlaskit/icon/core/subtasks"
import TaskIcon from "@atlaskit/icon/core/task"
import { Layers3, Wrench } from "lucide-react"

export function normalizeIssueTypeToken(value: string): string {
  return String(value || "").toLowerCase().replace(/[\s_-]+/g, "")
}

type WorkItemIconKind = "portfolio" | "story" | "subtask" | "defect" | "service" | "standard" | "custom"

const CANONICAL_KIND_BY_TOKEN: Record<string, WorkItemIconKind> = {
  portfolio: "portfolio",
  standard: "standard",
  defect: "defect",
  subtask: "subtask",
  service: "service",
  custom: "custom",
}

export interface WorkItemIconDescriptor {
  workItemType?: string | null
  providerType?: string | null
  issueType?: string | null
  issueTypeIconUrl?: string | null
}

const KIND_BY_PROVIDER_TOKEN: Record<string, WorkItemIconKind> = {
  epic: "portfolio",
  initiative: "portfolio",
  feature: "portfolio",
  milestone: "portfolio",
  project: "portfolio",
  newfeature: "portfolio",
  capability: "portfolio",

  story: "story",
  userstory: "story",
  requirement: "story",
  usecase: "story",

  task: "standard",
  issue: "standard",
  ticket: "standard",
  workitem: "standard",
  workrequest: "standard",
  request: "standard",
  chore: "standard",
  research: "standard",
  spike: "standard",
  qa: "standard",
  test: "standard",
  testing: "standard",
  sprint: "standard",
  problem: "standard",
  change: "standard",

  subtask: "subtask",
  subissue: "subtask",
  childtask: "subtask",
  childissue: "subtask",

  bug: "defect",
  defect: "defect",
  incident: "defect",
  hotfix: "defect",
  vulnerability: "defect",

  servicerequest: "service",
  service: "service",
  support: "service",
  ithelp: "service",
  helpdesk: "service",
}

function renderIconByKind(iconKind: WorkItemIconKind, fallbackLabel: string) {
  if (iconKind === "portfolio") {
    return <EpicIcon label={fallbackLabel || "Epic"} />
  }

  if (iconKind === "story") {
    return <StoryIcon label="Story" />
  }

  if (iconKind === "subtask") return <SubtasksIcon label="Sub-task" />
  if (iconKind === "defect") return <BugIcon label="Bug" />
  if (iconKind === "service") return <Wrench size={14} strokeWidth={2} aria-label="Service" />
  if (iconKind === "standard") return <TaskIcon label={fallbackLabel || "Task"} />
  return <Layers3 size={14} strokeWidth={2} aria-label="Custom" />
}

function resolveIconKindFromDescriptor(descriptor: WorkItemIconDescriptor): WorkItemIconKind {
  const providerToken = normalizeIssueTypeToken(String(descriptor.providerType || ""))
  const providerKind = providerToken ? (KIND_BY_PROVIDER_TOKEN[providerToken] || "custom") : null

  const issueTypeToken = normalizeIssueTypeToken(String(descriptor.issueType || ""))
  const issueTypeKind = issueTypeToken ? (KIND_BY_PROVIDER_TOKEN[issueTypeToken] || "custom") : null

  const canonicalToken = normalizeIssueTypeToken(String(descriptor.workItemType || ""))
  const canonicalKind = CANONICAL_KIND_BY_TOKEN[canonicalToken] || null

  // Prefer explicit provider-native type when known (e.g., Story should not collapse to Task icon).
  if (providerKind && providerKind !== "custom") return providerKind
  if (issueTypeKind && issueTypeKind !== "custom") return issueTypeKind

  // Fall back to canonical type class when provider/native type is absent or unknown.
  if (canonicalKind) return canonicalKind

  return "custom"
}

export function renderWorkItemIcon(descriptor: WorkItemIconDescriptor, fallbackLabel = "Task") {
  const iconKind = resolveIconKindFromDescriptor(descriptor)
  return renderIconByKind(iconKind, fallbackLabel)
}

export function renderIssueTypeIcon(issueType: string, fallbackLabel = "Task") {
  return renderWorkItemIcon({ issueType }, fallbackLabel)
}
