import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Terms of Service — ProMarshal',
  description: 'Terms governing your use of the ProMarshal platform.',
}

const LAST_UPDATED = 'March 25, 2026'
const CONTACT_EMAIL = 'legal@promarshal.ai'

const sections = [
  {
    id: 'acceptance',
    title: '1. Acceptance of Terms',
    content: `By accessing or using ProMarshal ("the Service"), you agree to be bound by these Terms of Service ("Terms"). If you are using ProMarshal on behalf of an organization, you represent that you have authority to bind that organization to these Terms, and "you" refers to that organization.

If you do not agree to these Terms, do not use the Service.`,
  },
  {
    id: 'description',
    title: '2. Description of Service',
    content: `ProMarshal is an AI-powered project management platform that automates coordination tasks for project teams. Core features include:

• Task Agent — an AI agent in Slack that creates, assigns, and queries tasks
• Cadence Agent — automated daily check-ins with team members
• Poll Agent — team pulse polls distributed via Slack
• Project Dashboard — real-time project health and task tracking
• Jira and Slack integrations for two-way task sync

ProMarshal is offered as a subscription service. Features available to you depend on your selected plan (Standard or Business).`,
  },
  {
    id: 'accounts',
    title: '3. Accounts and Registration',
    content: `To use ProMarshal, you must create an account using a valid email address. You are responsible for:

• Maintaining the security of your account credentials
• All activity that occurs under your account
• Notifying us immediately at ${CONTACT_EMAIL} if you suspect unauthorized access

You must be at least 18 years old to use ProMarshal. We reserve the right to terminate accounts that violate these Terms or are inactive for extended periods.`,
  },
  {
    id: 'subscriptions',
    title: '4. Subscriptions and Billing',
    subsections: [
      {
        subtitle: '4.1 Plans',
        text: `ProMarshal offers two subscription plans:

Standard — $30 per project per month (or $24 billed annually)
Includes: Task Agent, Cadence Agent, Poll Agent, Project Dashboard, Meeting Integration, Best Practices & Recommendations, and 14-day free trial.

Business — $225 per project per month (or $199 billed annually)
Includes: Everything in Standard, plus Project Planner, Unlimited Project Memory, Proactive AI Insights, Priority Support + SLA, and Dedicated Onboarding.`,
      },
      {
        subtitle: '4.2 Free Trial',
        text: 'Standard plan includes a 14-day free trial. No credit card is required during the trial period. At the end of the trial, your project will be paused unless you upgrade to a paid plan.',
      },
      {
        subtitle: '4.3 Billing Cycle',
        text: 'Subscriptions are billed per project, either monthly or annually. Annual plans are billed upfront for the full year. Pricing is subject to change with 30 days written notice.',
      },
      {
        subtitle: '4.4 Refunds',
        text: 'Monthly subscriptions are non-refundable for the current billing period. Annual subscriptions may be eligible for a prorated refund within 30 days of purchase if you are dissatisfied. Contact us at billing@promarshal.ai to request a refund.',
      },
      {
        subtitle: '4.5 Cancellation',
        text: 'You may cancel your subscription at any time from your account settings. Access continues until the end of the current billing period. We do not charge cancellation fees.',
      },
    ],
  },
  {
    id: 'acceptable-use',
    title: '5. Acceptable Use',
    content: `You agree not to use ProMarshal to:

• Violate any applicable law or regulation
• Upload or transmit malicious code, viruses, or harmful content
• Attempt to gain unauthorized access to our systems or another user's account
• Reverse engineer, decompile, or disassemble any part of the Service
• Scrape or extract data from the Service in bulk without our written consent
• Use the Service to send spam or unsolicited communications
• Impersonate any person or organization
• Engage in any activity that disrupts or degrades the performance of the Service

We reserve the right to suspend or terminate accounts that violate these guidelines, with or without notice.`,
  },
  {
    id: 'integrations',
    title: '6. Third-Party Integrations',
    content: `ProMarshal integrates with Slack and Jira. By connecting these services, you authorize ProMarshal to access and process data from those platforms on your behalf. Your use of those integrations is also subject to the terms of Slack and Atlassian respectively.

ProMarshal is not responsible for the availability, accuracy, or conduct of third-party services. If a third-party service changes its API or terms in a way that affects ProMarshal's functionality, we will make reasonable efforts to adapt but cannot guarantee continued compatibility.`,
  },
  {
    id: 'data-and-privacy',
    title: '7. Data and Privacy',
    content: `Your use of ProMarshal is also governed by our Privacy Policy, which is incorporated into these Terms by reference. By using the Service, you consent to our data practices as described in the Privacy Policy.

You retain ownership of all data you bring into ProMarshal (your tasks, projects, and team data). You grant ProMarshal a limited license to store, process, and use that data solely to deliver the Service to you. We will not use your data to train AI models without your explicit consent.`,
  },
  {
    id: 'ip',
    title: '8. Intellectual Property',
    content: `ProMarshal and all associated technology, software, design, and content are the property of ProMarshal and are protected by copyright, trademark, and other intellectual property laws.

You may not copy, reproduce, distribute, or create derivative works from any part of ProMarshal without our prior written consent.

You retain all intellectual property rights in the content and data you provide to ProMarshal. By submitting content, you grant us a non-exclusive, royalty-free license to use it solely to provide the Service.`,
  },
  {
    id: 'availability',
    title: '9. Service Availability',
    content: `We strive for high availability but do not guarantee that ProMarshal will be uninterrupted, error-free, or available 100% of the time. We may perform scheduled maintenance, which we will announce in advance where possible.

Business plan customers are covered by a Service Level Agreement (SLA) with guaranteed response times for support issues. SLA terms are provided at the time of Business plan activation.`,
  },
  {
    id: 'disclaimer',
    title: '10. Disclaimer of Warranties',
    content: `ProMarshal is provided "as is" and "as available" without warranties of any kind, either express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, or non-infringement.

We do not warrant that the Service will meet your specific requirements, that AI-generated content will be accurate, or that the Service will be free from errors or interruptions.`,
  },
  {
    id: 'liability',
    title: '11. Limitation of Liability',
    content: `To the fullest extent permitted by law, ProMarshal's total liability for any claim arising out of or related to these Terms or the Service shall not exceed the amount you paid to us in the 12 months prior to the claim.

We are not liable for any indirect, incidental, consequential, or punitive damages, including loss of profits, loss of data, or business interruption, even if advised of the possibility of such damages.

Some jurisdictions do not allow limitations on liability for certain types of damages. In such jurisdictions, our liability will be limited to the maximum extent permitted by law.`,
  },
  {
    id: 'indemnification',
    title: '12. Indemnification',
    content: `You agree to indemnify, defend, and hold harmless ProMarshal, its officers, directors, employees, and agents from any claims, liabilities, damages, and expenses (including reasonable legal fees) arising from:

• Your use of the Service
• Your violation of these Terms
• Your violation of any third-party rights
• Any content or data you submit to the Service`,
  },
  {
    id: 'termination',
    title: '13. Termination',
    content: `Either party may terminate the service relationship at any time. You may cancel your account through your account settings. We may terminate or suspend your access immediately if you violate these Terms.

Upon termination, your right to use the Service ends. We will retain your data for 30 days after termination, during which you may request an export. After 30 days, your data will be permanently deleted.`,
  },
  {
    id: 'governing-law',
    title: '14. Governing Law and Disputes',
    content: `These Terms are governed by applicable law. For EU/EEA users, mandatory consumer protection provisions of your local law also apply.

We encourage you to contact us first to resolve any dispute informally. If a dispute cannot be resolved informally, it will be submitted to binding arbitration, except where prohibited by law.

Nothing in these Terms prevents you from lodging a complaint with a relevant regulatory authority in your jurisdiction.`,
  },
  {
    id: 'changes',
    title: '15. Changes to Terms',
    content: `We may update these Terms from time to time. When we make material changes, we will notify you by email or via an in-app notice at least 14 days before the changes take effect.

Your continued use of ProMarshal after the effective date of changes constitutes your acceptance of the updated Terms. If you do not agree to the changes, you may cancel your account before they take effect.`,
  },
  {
    id: 'contact',
    title: '16. Contact',
    content: `For questions about these Terms, billing inquiries, or legal matters, contact us at:

ProMarshal
Email: ${CONTACT_EMAIL}

We will respond to all inquiries within 5 business days.`,
  },
]

export default function TermsPage() {
  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '80px 40px 120px' }}>

      {/* Header */}
      <div style={{ marginBottom: 64 }}>
        <div style={{
          display: 'inline-block',
          fontSize: 11, fontWeight: 700, letterSpacing: '2px',
          textTransform: 'uppercase', color: '#78a530',
          background: 'rgba(120,165,48,0.1)',
          border: '1px solid rgba(120,165,48,0.2)',
          borderRadius: 50, padding: '4px 14px', marginBottom: 20,
        }}>
          Legal
        </div>
        <h1 style={{
          fontSize: 'clamp(36px, 5vw, 56px)', fontWeight: 700,
          color: '#f0f4ff', letterSpacing: '-0.5px', lineHeight: 1.1,
          margin: '0 0 16px',
        }}>
          Terms of Service
        </h1>
        <p style={{ color: '#94a3b8', fontSize: 16, margin: 0 }}>
          Last updated: {LAST_UPDATED}
        </p>
      </div>

      {/* Callout */}
      <div style={{
        background: 'rgba(120,165,48,0.06)',
        border: '1px solid rgba(120,165,48,0.2)',
        borderRadius: 12, padding: '16px 20px', marginBottom: 64,
        display: 'flex', gap: 12, alignItems: 'flex-start',
      }}>
        <span style={{ fontSize: 18, flexShrink: 0, marginTop: 1 }}>📋</span>
        <p style={{ color: '#94a3b8', fontSize: 14, lineHeight: 1.7, margin: 0 }}>
          Please read these Terms carefully before using ProMarshal. By creating an account or using the Service, you agree to be bound by these Terms. If you have questions, contact us at <a href={`mailto:${CONTACT_EMAIL}`} style={{ color: '#78a530' }}>{CONTACT_EMAIL}</a>.
        </p>
      </div>

      {/* Table of Contents */}
      <div style={{
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 16, padding: '28px 32px', marginBottom: 64,
      }}>
        <p style={{ color: '#78a530', fontSize: 12, fontWeight: 700, letterSpacing: '1.5px', textTransform: 'uppercase', margin: '0 0 16px' }}>Contents</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 32px' }}>
          {sections.map(s => (
            <a key={s.id} href={`#${s.id}`} className="legal-toc-link">
              {s.title}
            </a>
          ))}
        </div>
      </div>

      {/* Sections */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 56 }}>
        {sections.map(section => (
          <div key={section.id} id={section.id}>
            <h2 style={{
              fontSize: 22, fontWeight: 700, color: '#f0f4ff',
              marginBottom: 20, paddingBottom: 12,
              borderBottom: '1px solid rgba(255,255,255,0.08)',
            }}>
              {section.title}
            </h2>

            {'content' in section && (
              <div style={{ color: '#94a3b8', fontSize: 15, lineHeight: 1.8, whiteSpace: 'pre-line' }}>
                {section.content}
              </div>
            )}

            {'subsections' in section && section.subsections && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
                {section.subsections.map(sub => (
                  <div key={sub.subtitle}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, color: '#e2e8f0', marginBottom: 10 }}>
                      {sub.subtitle}
                    </h3>
                    <p style={{ color: '#94a3b8', fontSize: 15, lineHeight: 1.8, whiteSpace: 'pre-line', margin: 0 }}>
                      {sub.text}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Contact CTA */}
      <div style={{
        marginTop: 80,
        background: 'linear-gradient(135deg, rgba(120,165,48,0.08), rgba(120,165,48,0.04))',
        border: '1px solid rgba(120,165,48,0.2)',
        borderRadius: 20, padding: '40px',
        textAlign: 'center',
      }}>
        <h3 style={{ fontSize: 20, fontWeight: 700, color: '#f0f4ff', margin: '0 0 12px' }}>
          Have questions about our terms?
        </h3>
        <p style={{ color: '#94a3b8', fontSize: 15, margin: '0 0 24px' }}>
          We&apos;re happy to clarify anything. Reach out and we&apos;ll get back to you within 5 business days.
        </p>
        <a href={`mailto:${CONTACT_EMAIL}`} style={{
          display: 'inline-block',
          background: '#78a530', color: '#fff',
          padding: '12px 28px', borderRadius: 50,
          fontSize: 15, fontWeight: 500, textDecoration: 'none',
        }}>
          Contact {CONTACT_EMAIL}
        </a>
      </div>
    </div>
  )
}
