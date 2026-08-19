"use client"

import Image from "next/image"
import Link from "next/link"
import { Check, ChevronDown } from "lucide-react"
import { siteConfig } from "@/config/site"
import { useState } from "react"

export default function PricingPage() {
    const [billingCycle, setBillingCycle] = useState<"monthly" | "yearly">("monthly")

    const plans = [
        {
            name: "Pro",
            description: "For teams ready to stop chasing updates",
            monthlyPrice: 49,
            yearlyPrice: 490,
            features: [
                "1 project with unlimited tasks",
                "Up to 15 team members",
                "AI-powered task reminders via Slack",
                "Real-time sync with Jira",
                "PM Board with team workload visibility",
                "Pulse dashboard with activity insights",
                "Smart team member discovery",
                "Slack conversation intelligence",
                "14-day free trial"
            ],
            highlighted: false,
            cta: "Get Early Access"
        },
        {
            name: "Business",
            description: "For teams that need complete project control",
            monthlyPrice: 99,
            yearlyPrice: 990,
            features: [
                "1 project with unlimited tasks",
                "Up to 30 team members",
                "Everything in Pro, plus:",
                "Cortex AI assistant for project insights",
                "Team activity heatmap—know when to reach anyone",
                "Stuck task detection—catch problems early",
                "Advanced analytics & velocity tracking",
                "Burndown charts & sprint metrics",
                "Priority email support",
                "14-day free trial"
            ],
            highlighted: true,
            cta: "Get Early Access"
        },
        {
            name: "Enterprise",
            description: "For organizations managing multiple projects",
            monthlyPrice: null,
            yearlyPrice: null,
            features: [
                "Unlimited projects",
                "Unlimited team members",
                "Everything in Business, plus:",
                "Cross-project portfolio dashboard",
                "Advanced team capacity planning",
                "Custom integration support",
                "Dedicated customer success manager",
                "Priority Slack/email support",
                "Custom onboarding & training"
            ],
            highlighted: false,
            cta: "Contact Sales"
        }
    ]

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
                        <nav className="hidden md:flex items-center gap-6">
                            <div className="relative group">
                                <button
                                    type="button"
                                    className="flex items-center gap-1 text-base font-semibold text-gray-700 hover:bg-[#78a530] hover:text-white transition px-3 py-1.5 rounded-md"
                                >
                                    Features
                                    <ChevronDown className="h-4 w-4" />
                                </button>
                                <div className="absolute left-0 top-full mt-2 w-48 bg-white rounded-lg shadow-lg border border-gray-200 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-50">
                                    <div className="py-2">
                                        <Link
                                            href="/project-hub"
                                            className="block px-4 py-2 text-sm font-medium text-gray-700 hover:bg-[#f5f6f8] hover:text-[#78a530] transition"
                                        >
                                            Project Hub
                                        </Link>
                                        <Link
                                            href="/#cadence"
                                            className="block px-4 py-2 text-sm font-medium text-gray-700 hover:bg-[#f5f6f8] hover:text-[#78a530] transition"
                                        >
                                            Cadence
                                        </Link>
                                    </div>
                                </div>
                            </div>
                            <Link
                                href="/pricing"
                                className="text-base font-semibold bg-[#78a530] text-white transition px-3 py-1.5 rounded-md"
                            >
                                Pricing
                            </Link>
                        </nav>
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
                {/* Hero Section */}
                <section className="relative overflow-hidden bg-[#f5f6f8] py-16 md:py-20 lg:py-24">
                    <div className="container mx-auto px-4">
                        <div className="mx-auto flex max-w-[58rem] flex-col items-center space-y-4 text-center">
                            <h1 className="text-4xl font-extrabold leading-tight tracking-tight text-black md:text-5xl lg:text-6xl">
                                Stop Managing. Start Leading.
                            </h1>
                            <p className="max-w-[750px] text-lg text-gray-600 md:text-2xl">
                                Let ProMarshal handle the coordination work while you focus on what matters. All plans include a 14-day free trial.
                                <br />
                                <span className="text-base md:text-lg font-semibold text-[#78a530] mt-2 inline-block">
                                    No credit card required to start.
                                </span>
                            </p>

                            {/* Billing Toggle */}
                            <div className="mt-8 inline-flex items-center rounded-full bg-white border-2 border-gray-200 p-1 shadow-md">
                                <button
                                    onClick={() => setBillingCycle("monthly")}
                                    className={`rounded-full px-6 py-2.5 text-sm font-semibold transition-all duration-200 ${billingCycle === "monthly"
                                        ? "bg-[#78a530] text-white"
                                        : "text-gray-700 hover:text-gray-900"
                                        }`}
                                >
                                    Monthly
                                </button>
                                <button
                                    onClick={() => setBillingCycle("yearly")}
                                    className={`rounded-full px-6 py-2.5 text-sm font-semibold transition-all duration-200 ${billingCycle === "yearly"
                                        ? "bg-[#78a530] text-white"
                                        : "text-gray-700 hover:text-gray-900"
                                        }`}
                                >
                                    Yearly
                                    <span className="ml-1.5 text-xs font-bold">Save 20%</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </section>

                {/* Pricing Cards */}
                <section className="bg-transparent py-12 md:py-16 lg:py-20">
                    <div className="container mx-auto px-4">
                        <div className="mx-auto grid max-w-7xl gap-8 lg:grid-cols-3 lg:gap-8">
                            {plans.map((plan, index) => (
                                <div
                                    key={index}
                                    className={`group relative overflow-hidden flex flex-col rounded-2xl border-2 bg-white p-8 shadow-xl transition-all duration-300 hover:-translate-y-2 ${plan.highlighted
                                        ? "border-[#78a530] shadow-[0_20px_50px_rgba(120,165,48,0.20)] lg:scale-105"
                                        : "border-gray-200 hover:border-[#d5e3ff] shadow-[0_12px_34px_rgba(24,86,221,0.10)] hover:shadow-[0_20px_44px_rgba(24,86,221,0.15)]"
                                        }`}
                                >
                                    {plan.highlighted && (
                                        <div className="absolute top-0 right-0 bg-[#78a530] text-white px-4 py-1 text-xs font-bold uppercase rounded-bl-lg">
                                            Popular
                                        </div>
                                    )}

                                    <div className="flex-1">
                                        {/* Plan Header */}
                                        <div className="mb-6">
                                            <h3 className="text-2xl font-bold text-gray-900 mb-2">{plan.name}</h3>
                                            <p className="text-sm text-gray-600">{plan.description}</p>
                                        </div>

                                        {/* Pricing */}
                                        <div className="mb-8">
                                            {plan.monthlyPrice !== null ? (
                                                <>
                                                    <div className="flex items-baseline gap-2">
                                                        <span className="text-5xl font-extrabold text-gray-900">
                                                            ${billingCycle === "monthly" ? plan.monthlyPrice : plan.yearlyPrice}
                                                        </span>
                                                        <span className="text-lg font-semibold text-gray-600">
                                                            /{billingCycle === "monthly" ? "month" : "year"}
                                                        </span>
                                                    </div>
                                                    {billingCycle === "yearly" && (
                                                        <p className="mt-2 text-sm text-[#78a530] font-semibold">
                                                            ${Math.round((plan.yearlyPrice / 12) * 100) / 100}/month billed annually
                                                        </p>
                                                    )}
                                                </>
                                            ) : (
                                                <div className="flex items-baseline gap-2">
                                                    <span className="text-4xl font-extrabold text-gray-900">
                                                        Custom Pricing
                                                    </span>
                                                </div>
                                            )}
                                        </div>

                                        {/* Features */}
                                        <ul className="space-y-3 mb-8">
                                            {plan.features.map((feature, featureIndex) => (
                                                <li key={featureIndex} className="flex items-start gap-3">
                                                    <Check
                                                        className={`h-5 w-5 mt-0.5 flex-shrink-0 ${plan.highlighted ? "text-[#78a530]" : "text-[#1856dd]"
                                                            }`}
                                                        strokeWidth={3}
                                                    />
                                                    <span className="text-sm text-gray-700">{feature}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    </div>

                                    {/* CTA Button */}
                                    {plan.cta === "Contact Sales" ? (
                                        <a
                                            href="mailto:sales@promarshal.ai?subject=Enterprise Plan Inquiry"
                                            className="inline-flex items-center justify-center rounded-full text-base font-semibold transition-all duration-200 h-12 px-6 shadow-lg gradient-primary text-white hover:opacity-90 shadow-[rgba(24,86,221,0.30)]"
                                        >
                                            {plan.cta}
                                            <svg
                                                className="ml-2 h-4 w-4"
                                                fill="none"
                                                stroke="currentColor"
                                                viewBox="0 0 24 24"
                                            >
                                                <path
                                                    strokeLinecap="round"
                                                    strokeLinejoin="round"
                                                    strokeWidth={2}
                                                    d="M13 7l5 5m0 0l-5 5m5-5H6"
                                                />
                                            </svg>
                                        </a>
                                    ) : (
                                        <Link
                                            href="/signup"
                                            className={`inline-flex items-center justify-center rounded-full text-base font-semibold transition-all duration-200 h-12 px-6 shadow-lg ${plan.highlighted
                                                ? "bg-[#78a530] text-white hover:bg-[#6a9329] shadow-[rgba(120,165,48,0.30)]"
                                                : "gradient-primary text-white hover:opacity-90 shadow-[rgba(24,86,221,0.30)]"
                                                }`}
                                        >
                                            {plan.cta}
                                            <svg
                                                className="ml-2 h-4 w-4"
                                                fill="none"
                                                stroke="currentColor"
                                                viewBox="0 0 24 24"
                                            >
                                                <path
                                                    strokeLinecap="round"
                                                    strokeLinejoin="round"
                                                    strokeWidth={2}
                                                    d="M13 7l5 5m0 0l-5 5m5-5H6"
                                                />
                                            </svg>
                                        </Link>
                                    )}
                                    {plan.cta === "Get Early Access" && (
                                        <p className="mt-3 text-center text-xs text-gray-500">
                                            Free during early access. Pricing applies later.
                                        </p>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                </section>

                {/* FAQ or Features Breakdown */}
                <section className="bg-white py-16 md:py-20 lg:py-24">
                    <div className="container mx-auto px-4 max-w-5xl">
                        <div className="text-center mb-12">
                            <h2 className="text-3xl md:text-4xl font-bold text-gray-900 mb-4">
                                Frequently Asked Questions
                            </h2>
                        </div>

                        <div className="space-y-6">
                            {[
                                {
                                    question: "What makes ProMarshal different from Asana, Monday.com, or Jira?",
                                    answer:
                                        "ProMarshal doesn't just track tasks—it actively prevents problems. Our AI reads your Slack conversations to surface critical decisions and blockers, detects stuck tasks before they become emergencies, and automatically discovers team members who should be invited to your project. It's the only PM tool that manages coordination work for you.",
                                },
                                {
                                    question: "How does the 14-day free trial work?",
                                    answer:
                                        "Start using ProMarshal immediately—no credit card required. You get full access to all features in your chosen plan. After 14 days, add a payment method to continue. All your data is preserved, and you pick up exactly where you left off.",
                                },
                                {
                                    question: "What integrations are included?",
                                    answer:
                                        "All plans include Slack and Jira integrations with full bidirectional sync, webhooks, and real-time updates. ProMarshal reads your Slack channels to surface important conversations, syncs tasks from Jira automatically, and keeps everything up to date in real-time.",
                                },
                                {
                                    question: "Can I switch plans as my team grows?",
                                    answer:
                                        "Absolutely. Upgrade or downgrade anytime from your billing settings. Changes take effect immediately, and we'll prorate the difference. As your team scales, you can move from Growth to Scale, or contact us for Enterprise pricing.",
                                },
                                {
                                    question: "What happens if I cancel?",
                                    answer:
                                        "You can cancel anytime. You'll keep access until the end of your billing period, and we won't charge you again. Your data remains safely stored for 30 days in case you want to return.",
                                },
                            ].map((faq, index) => (
                                <div
                                    key={index}
                                    className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-shadow"
                                >
                                    <h3 className="text-lg font-bold text-gray-900 mb-2">{faq.question}</h3>
                                    <p className="text-gray-600 leading-relaxed">{faq.answer}</p>
                                </div>
                            ))}
                        </div>
                    </div>
                </section>

                {/* CTA Section */}
                <section className="container mx-auto px-4 py-12 md:py-16 lg:py-20 bg-transparent">
                    <div className="mx-auto flex max-w-[58rem] flex-col items-center space-y-6 text-center rounded-3xl bg-gradient-to-br from-[#e8f0ff] to-[#dbe8ff] p-12 md:p-16 border border-[#e3ecff]">
                        <h2 className="font-extrabold text-3xl leading-tight text-gray-900 sm:text-4xl md:text-5xl">
                            Ready to Get Started?
                        </h2>
                        <p className="max-w-[85%] text-lg text-gray-600 md:text-xl">
                            Start your 14-day free trial today. No credit card required.
                        </p>
                        <Link
                            href="/signup"
                            className="inline-flex items-center justify-center rounded-full text-base font-semibold gradient-primary text-white hover:opacity-90 transition-all duration-200 h-14 px-10 shadow-xl shadow-[rgba(24,86,221,0.40)] mt-4"
                        >
                            Start Free Trial
                            <svg className="ml-2 h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    strokeWidth={2}
                                    d="M13 7l5 5m0 0l-5 5m5-5H6"
                                />
                            </svg>
                        </Link>
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
