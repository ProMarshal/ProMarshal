import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Privacy Policy — ProMarshal',
  description: 'How ProMarshal collects, uses, and protects your data.',
}

const LAST_UPDATED = 'March 25, 2026'
const CONTACT_EMAIL = 'legal@promarshal.ai'

const sections = [
  {
    id: 'overview',
    title: '1. Overview',
    content: `ProMarshal ("we", "our", or "us") is a B2B SaaS platform that helps project managers automate coordination tasks through AI agents in Slack. We also offer a Chrome browser extension that captures meeting audio and transcripts from Google Meet calls to generate meeting minutes, action items, and project insights. This Privacy Policy explains what personal data we collect, why we collect it, how we use it, and your rights regarding that data.

By using ProMarshal, you agree to the practices described in this policy. If you do not agree, please do not use our service.`,
  },
  {
    id: 'data-collected',
    title: '2. Data We Collect',
    subsections: [
      {
        subtitle: '2.1 Account Information',
        text: 'When you sign up, we collect your name and email address. These are used to create and manage your account, send OTP verification codes, and communicate with you about the service.',
      },
      {
        subtitle: '2.2 Integration Data',
        text: `When you connect ProMarshal to Slack or Jira, we collect and store:
• OAuth access tokens and refresh tokens (encrypted at rest)
• Slack workspace ID, team name, and channel information
• Jira site URL, cloud ID, and project/issue data
• Task titles, statuses, assignees, due dates, and comments

These are required to deliver core functionality — sending reminders, syncing tasks, and handling status updates.`,
      },
      {
        subtitle: '2.3 Usage Data',
        text: 'We collect basic technical data including IP address, browser type, pages visited, and feature usage. This helps us maintain service reliability and improve the product.',
      },
      {
        subtitle: '2.4 AI Processing Data',
        text: 'For paid tier users, task and project data may be sent to third-party AI providers (OpenAI, Anthropic) to generate conversational reminders and insights. This data is processed under our agreements with those providers and is not used to train their models.',
      },
      {
        subtitle: '2.5 Meeting Audio and Transcripts (Browser Extension)',
        text: `When you install our Chrome browser extension and explicitly start a recording in a Google Meet call, we capture your microphone audio, the meeting tab's audio, and the live captions that Google Meet generates. Audio is streamed to our backend in real time and converted to text by our speech-to-text provider (see Section 4). We store the resulting transcripts, speaker timelines, and meeting metadata (start time, duration, and the project you associate with the meeting). We do not retain the raw audio after transcription.

Recording requires explicit user action: you must click "Yes" on the project prompt that appears when you join a Meet call. If you click "No" or dismiss the prompt, nothing is captured.`,
      },
    ],
  },
  {
    id: 'how-we-use',
    title: '3. How We Use Your Data',
    content: `We use your data solely to provide and improve ProMarshal:

• Deliver automated task reminders via Slack
• Sync task data between Jira and our system
• Send transactional emails (OTP codes, account notifications) via SendGrid
• Generate AI-powered insights and reminders (paid tier)
• Monitor system health and prevent abuse
• Respond to support requests

We do not sell your data. We do not use your data for advertising.`,
  },
  {
    id: 'third-parties',
    title: '4. Third-Party Services',
    content: `ProMarshal uses the following third-party processors to deliver our service:

| Service | Purpose | Data Shared |
|---------|---------|-------------|
| Slack | Task reminders, team interactions | Workspace tokens, messages |
| Jira (Atlassian) | Task sync and status updates | Project/issue data, OAuth tokens |
| MongoDB Atlas | Database storage | All user and project data |
| OpenAI | AI message generation (paid tier) | Task titles, descriptions |
| Anthropic | AI message generation (alternative) | Task titles, descriptions |
| Deepgram | Real-time speech-to-text transcription (browser extension) | Meeting audio streamed during active recording |
| SendGrid | Transactional email delivery | Email address |
| Render | Backend hosting | All data in transit |
| Google | OAuth sign-in | Name, email |

Each provider is bound by their own privacy policy and data processing agreements. Links to their policies are available on their respective websites.`,
  },
  {
    id: 'data-retention',
    title: '5. Data Retention',
    content: `We retain your data for as long as your account is active. Upon account deletion:

• Your account and profile data is deleted immediately
• Integration tokens are revoked and deleted immediately
• Project and task data is deleted immediately
• Backup copies may persist for up to 30 days in encrypted storage before permanent deletion

You can delete your account and all associated data instantly through the ProMarshal dashboard. Upon deletion, all your data is permanently removed from our systems with no residual copies beyond encrypted backups which are purged within 30 days.`,
  },
  {
    id: 'security',
    title: '6. Security',
    content: `We take security seriously:

• All data is transmitted over HTTPS/TLS
• OAuth tokens are encrypted at rest using AES-256 encryption
• Access to production data is restricted to authorized personnel
• We conduct regular security reviews

While we implement industry-standard protections, no system is 100% secure. In the event of a data breach affecting your personal data, we will notify you as required by applicable law.`,
  },
  {
    id: 'cookies',
    title: '7. Cookies',
    content: `ProMarshal uses a minimal set of cookies, all of which are strictly necessary for the service to function:

• next-auth.session-token — Maintains your login session
• next-auth.csrf-token — Protects against cross-site request forgery

These cookies do not track you across websites and do not require your consent under any privacy regulation, as they are essential to providing the service you have requested.

We do not use advertising cookies, tracking pixels, or third-party analytics cookies.`,
  },
  {
    id: 'gdpr',
    title: '8. Your Rights (EU/UK — GDPR)',
    content: `If you are located in the EU, EEA, or United Kingdom, you have the following rights under GDPR:

• Right to Access — Request a copy of your personal data
• Right to Rectification — Correct inaccurate data
• Right to Erasure — Request deletion of your data ("right to be forgotten")
• Right to Restriction — Limit how we process your data
• Right to Data Portability — Receive your data in a machine-readable format
• Right to Object — Object to processing based on legitimate interests
• Right to Withdraw Consent — Where processing is based on consent

Our lawful basis for processing is primarily contract performance (delivering the service you signed up for) and legitimate interests (service security and reliability).

To exercise any right, contact us at ${CONTACT_EMAIL}. We will respond within 30 days. You also have the right to lodge a complaint with your local data protection authority.`,
  },
  {
    id: 'ccpa',
    title: '9. Your Rights (California — CCPA/CPRA)',
    content: `If you are a California resident, you have the right to:

• Know what personal data we collect and why
• Request deletion of your personal data
• Correct inaccurate personal data
• Non-discrimination for exercising your rights

ProMarshal does not sell personal information and does not share personal information for cross-context behavioral advertising. Therefore, a "Do Not Sell or Share" opt-out is not applicable.

To submit a request, contact us at ${CONTACT_EMAIL}.`,
  },
  {
    id: 'other-regions',
    title: '10. Other Regions',
    content: `Canada (PIPEDA / Quebec Law 25): We provide equivalent data subject rights to Canadian users, including access, correction, and deletion. Quebec users may exercise rights under Law 25 by contacting us directly.

Brazil (LGPD): Brazilian users have rights to confirmation of processing, access, correction, deletion, and portability under the LGPD. Contact us to exercise these rights.

Australia (Privacy Act): We comply with the Australian Privacy Principles. Australian users may request access to or correction of their data at any time.

India (DPDP Act 2023): We respect Indian users' rights to access and correction of personal data as provided under applicable Indian law.

For all regions, contact us at ${CONTACT_EMAIL} to exercise your rights.`,
  },
  {
    id: 'children',
    title: '11. Children\'s Privacy',
    content: `ProMarshal is a B2B service intended for business users aged 18 and over. We do not knowingly collect personal data from children under 16. If you believe a child has provided us with personal data, please contact us and we will delete it immediately.`,
  },
  {
    id: 'changes',
    title: '12. Changes to This Policy',
    content: `We may update this Privacy Policy from time to time. When we make material changes, we will notify you by email or by displaying a notice in the application. The "Last Updated" date at the top of this page reflects the most recent revision.

Continued use of ProMarshal after changes take effect constitutes acceptance of the updated policy.`,
  },
  {
    id: 'contact',
    title: '13. Contact Us',
    content: `For any privacy-related questions, data subject requests, or concerns, contact us at:

ProMarshal
Email: ${CONTACT_EMAIL}

We aim to respond to all inquiries within 5 business days and to fulfill all data subject requests within 30 days.`,
  },
]

export default function PrivacyPage() {
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
          Privacy Policy
        </h1>
        <p style={{ color: '#94a3b8', fontSize: 16, margin: 0 }}>
          Last updated: {LAST_UPDATED}
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
          Questions about your privacy?
        </h3>
        <p style={{ color: '#94a3b8', fontSize: 15, margin: '0 0 24px' }}>
          We&apos;re happy to help. Reach out and we&apos;ll respond within 5 business days.
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
