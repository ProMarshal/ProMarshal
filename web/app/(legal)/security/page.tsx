import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Security — ProMarshal',
  description: 'ProMarshal security practices and vulnerability disclosure program.',
}

const LAST_UPDATED = 'April 9, 2026'
const SECURITY_EMAIL = 'support@promarshal.ai'

export default function SecurityPage() {
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
          Security
        </div>
        <h1 style={{
          fontSize: 'clamp(36px, 5vw, 56px)', fontWeight: 700,
          color: '#f0f4ff', letterSpacing: '-0.5px', lineHeight: 1.1,
          margin: '0 0 16px',
        }}>
          Security at ProMarshal
        </h1>
        <p style={{ color: '#94a3b8', fontSize: 16, margin: 0 }}>
          Last updated: {LAST_UPDATED}
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 56 }}>

        {/* Security Practices */}
        <div>
          <h2 style={h2Style}>Our Security Practices</h2>
          <div style={bodyStyle}>
            {`ProMarshal takes the security of our platform and customer data seriously. We implement the following measures:

• All data is transmitted over HTTPS/TLS
• OAuth tokens and credentials are encrypted at rest using AES-256 encryption
• Access to production systems is restricted to authorized personnel only
• Regular security reviews and dependency updates
• Secrets are managed via environment variables, never hardcoded in source code
• MongoDB Atlas database encryption at rest
• Slack request signing verification on all webhook endpoints`}
          </div>
        </div>

        {/* Vulnerability Disclosure Program */}
        <div>
          <h2 style={h2Style}>Vulnerability Disclosure Program</h2>
          <div style={bodyStyle}>
            {`ProMarshal operates a vulnerability disclosure program. If you discover a security vulnerability in our platform or Slack app, we encourage you to report it responsibly.

How to report:
• Email us at ${SECURITY_EMAIL} with details of the vulnerability
• Include steps to reproduce, potential impact, and any supporting evidence
• We will acknowledge your report within 48 hours
• We aim to resolve confirmed vulnerabilities within 30 days
• We will keep you informed of our progress throughout the process

We ask that you:
• Do not publicly disclose the vulnerability before we have had a chance to address it
• Do not access, modify, or delete data that does not belong to you
• Act in good faith to avoid privacy violations or service disruption

We appreciate the security community's efforts in keeping ProMarshal safe.`}
          </div>
        </div>

        {/* Contact */}
        <div>
          <h2 style={h2Style}>Contact</h2>
          <div style={bodyStyle}>
            {`For security vulnerability reports and security-related inquiries, contact us at ${SECURITY_EMAIL}.

For general privacy or data-related questions, contact support@promarshal.ai.`}
          </div>
        </div>

      </div>

      {/* CTA */}
      <div style={{
        marginTop: 80,
        background: 'linear-gradient(135deg, rgba(120,165,48,0.08), rgba(120,165,48,0.04))',
        border: '1px solid rgba(120,165,48,0.2)',
        borderRadius: 20, padding: '40px',
        textAlign: 'center',
      }}>
        <h3 style={{ fontSize: 20, fontWeight: 700, color: '#f0f4ff', margin: '0 0 12px' }}>
          Found a security issue?
        </h3>
        <p style={{ color: '#94a3b8', fontSize: 15, margin: '0 0 24px' }}>
          Please report it responsibly. We will acknowledge within 48 hours.
        </p>
        <a href={`mailto:${SECURITY_EMAIL}`} style={{
          display: 'inline-block',
          background: '#78a530', color: '#fff',
          padding: '12px 28px', borderRadius: 50,
          fontSize: 15, fontWeight: 500, textDecoration: 'none',
        }}>
          Report to {SECURITY_EMAIL}
        </a>
      </div>

    </div>
  )
}

const h2Style: React.CSSProperties = {
  fontSize: 22, fontWeight: 700, color: '#f0f4ff',
  marginBottom: 20, paddingBottom: 12,
  borderBottom: '1px solid rgba(255,255,255,0.08)',
}

const bodyStyle: React.CSSProperties = {
  color: '#94a3b8', fontSize: 15, lineHeight: 1.8, whiteSpace: 'pre-line',
}
