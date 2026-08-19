"use client"

import { useEffect, useState } from "react"
import Image from "next/image"

interface WelcomeScreenProps {
  userName: string
  onContinue: () => void
}

export function WelcomeScreen({ userName, onContinue }: WelcomeScreenProps) {
  const firstName = userName?.split(" ")[0] || "there"
  const [isDarkMode, setIsDarkMode] = useState(true)

  useEffect(() => {
    const saved = localStorage.getItem("promarshal_dark_mode")
    if (saved !== null) setIsDarkMode(saved === "true")
  }, [])

  const dm = isDarkMode

  return (
    <div className={`flex min-h-screen px-6 py-12 ${dm ? "bg-[#111520]" : "bg-gray-100"}`}>
      <div className="w-full max-w-xl mx-auto">
        {/* Logo */}
        <div className="mb-10 flex justify-center">
          <div className="w-16 h-16">
            <Image
              src={dm ? "/logos/logo-white.svg" : "/logos/logo-black.svg"}
              alt="ProMarshal"
              width={64}
              height={64}
              className="w-full h-full object-contain"
            />
          </div>
        </div>

        {/* Content */}
        <div className="flex flex-col items-center text-center space-y-6 mt-16">
          <h1 className={`text-2xl font-bold ${dm ? "text-gray-100" : "text-gray-900"}`}>
            Welcome {firstName}
          </h1>

          <p className={`text-xl font-medium ${dm ? "text-gray-300" : "text-gray-700"}`}>
            Thank you for choosing me as your project assistant.
          </p>

          <p className={`text-xl font-semibold ${dm ? "text-gray-100" : "text-gray-800"}`}>
            Let us setup your project space...
          </p>

          <button
            type="button"
            onClick={onContinue}
            className="mt-6 rounded-full px-10 py-3 text-base font-semibold text-white transition hover:-translate-y-0.5 hover:shadow-xl"
            style={{ backgroundColor: "#78a530" }}
          >
            Let's Go
          </button>
        </div>
      </div>
    </div>
  )
}
