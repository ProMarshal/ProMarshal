import Image from "next/image"
import Link from "next/link"
import { CheckSquare, Eye, Brain, Bell } from "lucide-react"
import { siteConfig } from "@/config/site"

export default function ProjectHubPage() {
  return (
    <div className="flex min-h-screen flex-col bg-[#f5f6f8]">
      {/* Header */}
      <header className="sticky top-0 z-50 w-full border-b border-gray-200 bg-[#f5f6f8]/95 backdrop-blur">
        <div className="container mx-auto px-4 flex h-16 items-center">
          <div className="mr-4 flex items-center gap-8">
            <Link href="/" className="flex items-center space-x-3">
              <Image
                src="/logos/logo-black.svg"
                alt="ProMarshal"
                width={32}
                height={32}
                className="w-8 h-8 object-contain"
              />
              <span className="font-black text-2xl text-black">{siteConfig.name}</span>
            </Link>
          </div>
          <div className="flex flex-1 items-center justify-end space-x-4">
            <nav className="flex items-center space-x-3">
              <Link
                href="/signup"
                className="inline-flex items-center justify-center rounded-full text-sm font-semibold gradient-primary text-white hover:opacity-90 transition-all duration-200 h-11 px-8 shadow-lg shadow-[rgba(24,86,221,0.30)]"
              >
                Get Started
              </Link>
            </nav>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1">
        <section className="bg-white py-16 md:py-20 lg:py-24">
          <div className="container mx-auto px-4 max-w-6xl">
            {/* Hero */}
            <div className="text-center mb-16">
              <h1 className="font-extrabold text-4xl md:text-5xl lg:text-6xl text-gray-900 mb-4">
                Your Project Command Center
              </h1>
              <p className="text-xl md:text-2xl text-gray-600 max-w-3xl mx-auto">
                Everything you need to manage projects effectively. Track progress, identify risks, and keep your team aligned—all in one dashboard.
              </p>
            </div>

            {/* Demo Video - Centered and Prominent */}
            <div className="mb-20">
              <div className="relative w-full max-w-5xl mx-auto">
                <div className="aspect-video rounded-xl overflow-hidden shadow-2xl border border-gray-200 bg-gray-100">
                  <iframe
                    src="https://www.youtube.com/embed/YOUR_VIDEO_ID"
                    title="ProMarshal Project Hub Demo"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    allowFullScreen
                    className="w-full h-full"
                  />
                </div>
              </div>
            </div>

            {/* Key Capabilities */}
            <div className="mb-20">
              <h2 className="text-3xl md:text-4xl font-bold text-gray-900 text-center mb-12">
                What's Inside Project Hub
              </h2>
              <div className="space-y-20">
                {/* PM Board */}
                <div className="flex flex-col md:flex-row items-center gap-12">
                  <div className="flex-1">
                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-[#78a530] text-white mb-6">
                      <CheckSquare className="w-7 h-7" />
                    </div>
                    <h3 className="text-3xl font-bold text-gray-900 mb-4">PM Board</h3>
                    <p className="text-xl text-gray-600 mb-6 leading-relaxed">
                      Your central dashboard for complete project visibility. See every task, track every team member's workload, and catch potential issues before they become problems.
                    </p>
                    <ul className="space-y-3 text-lg text-gray-600">
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">High-level team and workload visibility</strong> — See who's working on what, who's overloaded, and who has capacity at a glance</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Real-time sync with PM tools</strong> — Your tasks from Jira automatically appear here, always up to date</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Smart team member discovery</strong> — ProMarshal finds people assigned to tasks and suggests inviting them to your project</span>
                      </li>
                    </ul>
                  </div>
                  <div className="flex-1 bg-gray-100 rounded-xl h-80 flex items-center justify-center border-2 border-gray-200">
                    <span className="text-gray-400 text-lg">PM Board Screenshot</span>
                  </div>
                </div>

                {/* Pulse */}
                <div className="flex flex-col md:flex-row-reverse items-center gap-12">
                  <div className="flex-1">
                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-[#78a530] text-white mb-6">
                      <Eye className="w-7 h-7" />
                    </div>
                    <h3 className="text-3xl font-bold text-gray-900 mb-4">Pulse</h3>
                    <p className="text-xl text-gray-600 mb-6 leading-relaxed">
                      Stop chasing updates. Pulse automatically captures what matters—from Slack conversations to task movements—and shows you exactly where your project stands.
                    </p>
                    <ul className="space-y-3 text-lg text-gray-600">
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Team Activity Heatmap</strong> — Visual grid showing when your team is most active across days and hours, so you know the best times to reach them</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Slack Conversation Intelligence</strong> — ProMarshal reads your channels and automatically surfaces critical decisions, blockers, and action items—no more scrolling through messages</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Stuck Task Detection</strong> — Identifies tasks that haven't moved in days and alerts you before they become critical problems</span>
                      </li>
                    </ul>
                  </div>
                  <div className="flex-1 bg-gray-100 rounded-xl h-80 flex items-center justify-center border-2 border-gray-200">
                    <span className="text-gray-400 text-lg">Pulse Screenshot</span>
                  </div>
                </div>
              </div>
            </div>

            {/* CTA */}
            <div className="text-center">
              <Link
                href="/signup"
                className="inline-flex items-center justify-center rounded-full text-base font-semibold gradient-primary text-white hover:opacity-90 transition-all duration-200 h-14 px-10 shadow-xl shadow-[rgba(24,86,221,0.40)]"
              >
                Get Started with Project Hub
                <svg className="ml-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                </svg>
              </Link>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-200 py-8 md:py-12 bg-transparent">
        <div className="container mx-auto px-4 flex flex-col items-center justify-between gap-6 md:flex-row">
          <div className="flex items-center gap-2">
            <span className="font-bold text-lg gradient-text">{siteConfig.name}</span>
          </div>
          <p className="text-center text-sm text-gray-600 md:text-left">
            © 2025 ProMarshal. Built for project managers who value their time.
          </p>
        </div>
      </footer>
    </div>
  )
}
