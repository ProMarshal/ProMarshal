"use client"

import { X } from "lucide-react"

interface ConfirmationModalProps {
  isOpen: boolean
  isDarkMode?: boolean
  title: string
  description: string
  highlightText?: string
  primaryLabel: string
  secondaryLabel: string
  primaryVariant?: "danger" | "success"
  isPrimaryLoading?: boolean
  onPrimary: () => void
  onSecondary: () => void
  onClose: () => void
}

export function ConfirmationModal({
  isOpen,
  isDarkMode = false,
  title,
  description,
  highlightText,
  primaryLabel,
  secondaryLabel,
  primaryVariant = "success",
  isPrimaryLoading = false,
  onPrimary,
  onSecondary,
  onClose,
}: ConfirmationModalProps) {
  if (!isOpen) return null

  const dm = isDarkMode
  const glassStyle = dm
    ? {
        background: "linear-gradient(135deg, rgba(30,38,56,0.95) 0%, rgba(17,21,32,0.98) 100%)",
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
        border: "1px solid rgba(255,255,255,0.18)",
        boxShadow: "0 8px 40px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.12)",
      }
    : {}

  const primaryClass =
    primaryVariant === "danger"
      ? "bg-red-600 text-white hover:bg-red-700"
      : "bg-[#78a530] text-white hover:bg-[#6a9129]"

  return (
    <>
      <div className="fixed inset-0 bg-black/60 z-40" onClick={onClose} />

      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div
          className={`rounded-2xl shadow-2xl max-w-lg w-full pointer-events-auto ${
            dm ? "" : "bg-white border-2 border-gray-300"
          }`}
          style={dm ? glassStyle : {}}
        >
          <div className={`flex items-center justify-between p-6 border-b ${dm ? "border-white/[0.08]" : "border-gray-200"}`}>
            <h2 className={`text-xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>{title}</h2>
            <button
              onClick={onClose}
              className={`p-2 rounded-lg transition ${dm ? "hover:bg-white/[0.08] text-gray-400" : "hover:bg-gray-100 text-gray-500"}`}
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="p-6">
            <p className={`text-sm leading-6 ${dm ? "text-gray-300" : "text-gray-700"}`}>{description}</p>
            {highlightText && (
              <div
                className={`mt-4 rounded-xl border px-4 py-3 text-sm ${
                  dm
                    ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                    : "border-amber-200 bg-amber-50 text-amber-900"
                }`}
              >
                {highlightText}
              </div>
            )}
          </div>

          <div className="flex gap-3 px-6 pb-6">
            <button
              onClick={onSecondary}
              disabled={isPrimaryLoading}
              className={`flex-1 px-6 py-3 border-2 rounded-xl font-semibold transition disabled:opacity-50 ${
                dm ? "border-white/[0.12] text-gray-300 hover:bg-white/[0.06]" : "border-gray-300 text-gray-700 hover:bg-gray-100"
              }`}
            >
              {secondaryLabel}
            </button>
            <button
              onClick={onPrimary}
              disabled={isPrimaryLoading}
              className={`flex-1 px-6 py-3 rounded-xl font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed ${primaryClass}`}
            >
              {isPrimaryLoading ? "Please wait..." : primaryLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

