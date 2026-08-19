import Image from "next/image"
import Link from "next/link"
import { Bell, MessageSquare, Zap, Clock } from "lucide-react"
import { siteConfig } from "@/config/site"

export default function CadencePage() {
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
                Your AI Project Assistant
              </h1>
              <p className="text-xl md:text-2xl text-gray-600 max-w-3xl mx-auto">
                ProMarshal Cadence keeps your team in sync with intelligent reminders, AI-powered insights, and instant actions—all through Slack.
              </p>
            </div>

            {/* Demo Video - Centered and Prominent */}
            <div className="mb-20">
              <div className="relative w-full max-w-5xl mx-auto">
                <div className="aspect-video rounded-xl overflow-hidden shadow-2xl border border-gray-200 bg-gray-100">
                  <iframe
                    src="https://www.youtube.com/embed/YOUR_VIDEO_ID"
                    title="ProMarshal Cadence Demo"
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
                What's Inside Cadence
              </h2>
              <div className="space-y-20">
                {/* Daily Task Reminders */}
                <div className="flex flex-col md:flex-row items-center gap-12">
                  <div className="flex-1">
                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-[#78a530] text-white mb-6">
                      <Bell className="w-7 h-7" />
                    </div>
                    <h3 className="text-3xl font-bold text-gray-900 mb-4">Daily Task Reminders</h3>
                    <p className="text-xl text-gray-600 mb-6 leading-relaxed">
                      Stop chasing your team for updates. ProMarshal sends personalized task reminders directly to each team member in Slack—automatically, every day.
                    </p>
                    <ul className="space-y-3 text-lg text-gray-600">
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Smart scheduling</strong> — Reminders sent at the right time based on your project's timezone</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Interactive conversations</strong> — Team members reply directly in Slack to update task status and add comments</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Automatic sync to Jira</strong> — Updates flow back to Jira instantly, no manual work required</span>
                      </li>
                    </ul>
                  </div>
                  <div className="flex-1 bg-gray-100 rounded-xl h-80 flex items-center justify-center border-2 border-gray-200">
                    <span className="text-gray-400 text-lg">Daily Reminders Screenshot</span>
                  </div>
                </div>

                {/* Cortex AI */}
                <div className="flex flex-col md:flex-row-reverse items-center gap-12">
                  <div className="flex-1">
                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-[#78a530] text-white mb-6">
                      <MessageSquare className="w-7 h-7" />
                    </div>
                    <h3 className="text-3xl font-bold text-gray-900 mb-4">Cortex AI Assistant</h3>
                    <p className="text-xl text-gray-600 mb-6 leading-relaxed">
                      Your AI project analyst. Cortex reads your project data, understands context, and provides intelligent insights when you need them.
                    </p>
                    <ul className="space-y-3 text-lg text-gray-600">
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Ask anything about your project</strong> — "What's blocking the team?", "Who's overloaded?", "What changed today?"</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Contextual insights</strong> — Cortex knows your tasks, team workload, and project history</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Proactive alerts</strong> — Get notified when Cortex spots risks, delays, or bottlenecks</span>
                      </li>
                    </ul>
                  </div>
                  <div className="flex-1 bg-gray-100 rounded-xl h-80 flex items-center justify-center border-2 border-gray-200">
                    <span className="text-gray-400 text-lg">Cortex AI Screenshot</span>
                  </div>
                </div>

                {/* Slash Commands */}
                <div className="flex flex-col md:flex-row items-center gap-12">
                  <div className="flex-1">
                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-[#78a530] text-white mb-6">
                      <Zap className="w-7 h-7" />
                    </div>
                    <h3 className="text-3xl font-bold text-gray-900 mb-4">Slack Slash Commands</h3>
                    <p className="text-xl text-gray-600 mb-6 leading-relaxed">
                      Manage your project without leaving Slack. Create tasks, update status, and check progress with simple commands—no context switching needed.
                    </p>
                    <ul className="space-y-3 text-lg text-gray-600">
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">/promarshal create-task</strong> — Open a form to create new tasks instantly</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">/promarshal list-tasks</strong> — See your active tasks right in Slack</span>
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="text-[#78a530] mt-1 text-xl font-bold">✓</span>
                        <span><strong className="text-gray-900">Fast and intuitive</strong> — Everything syncs to Jira automatically</span>
                      </li>
                    </ul>
                  </div>
                  <div className="flex-1 bg-gray-100 rounded-xl h-80 flex items-center justify-center border-2 border-gray-200">
                    <span className="text-gray-400 text-lg">Slash Commands Screenshot</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Why Cadence Works */}
            <div className="mb-20 bg-gradient-to-br from-[#e8f0ff] to-[#dbe8ff] rounded-3xl p-10 md:p-14 border border-[#e3ecff]">
              <div className="text-center mb-10">
                <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">
                  Why Cadence Changes Everything
                </h2>
                <p className="text-xl text-gray-600 max-w-3xl mx-auto">
                  Your team already lives in Slack. ProMarshal Cadence meets them where they are.
                </p>
              </div>
              <div className="grid md:grid-cols-3 gap-8 max-w-5xl mx-auto">
                <div className="bg-white rounded-xl p-6 shadow-sm">
                  <div className="inline-flex items-center justify-center w-12 h-12 rounded-lg bg-[#78a530] text-white mb-4">
                    <Clock className="w-6 h-6" />
                  </div>
                  <h3 className="text-xl font-bold text-gray-900 mb-3">Save 10+ Hours Per Week</h3>
                  <p className="text-gray-600">
                    No more status meetings or chasing updates. ProMarshal handles coordination automatically.
                  </p>
                </div>
                <div className="bg-white rounded-xl p-6 shadow-sm">
                  <div className="inline-flex items-center justify-center w-12 h-12 rounded-lg bg-[#78a530] text-white mb-4">
                    <MessageSquare className="w-6 h-6" />
                  </div>
                  <h3 className="text-xl font-bold text-gray-900 mb-3">Zero Context Switching</h3>
                  <p className="text-gray-600">
                    Your team updates tasks in Slack. No need to open Jira or another tool.
                  </p>
                </div>
                <div className="bg-white rounded-xl p-6 shadow-sm">
                  <div className="inline-flex items-center justify-center w-12 h-12 rounded-lg bg-[#78a530] text-white mb-4">
                    <Zap className="w-6 h-6" />
                  </div>
                  <h3 className="text-xl font-bold text-gray-900 mb-3">Always Up to Date</h3>
                  <p className="text-gray-600">
                    Real-time sync with Jira means everyone sees the latest status, automatically.
                  </p>
                </div>
              </div>
            </div>

            {/* CTA */}
            <div className="text-center">
              <Link
                href="/signup"
                className="inline-flex items-center justify-center rounded-full text-base font-semibold gradient-primary text-white hover:opacity-90 transition-all duration-200 h-14 px-10 shadow-xl shadow-[rgba(24,86,221,0.40)]"
              >
                Get Started with Cadence
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
