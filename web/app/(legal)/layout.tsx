import Link from 'next/link'
import Image from 'next/image'

export default function LegalLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ background: '#000', minHeight: '100vh', color: '#f0f4ff', fontFamily: 'var(--font-inter), sans-serif' }}>
      {/* Nav */}
      <nav style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 40px', height: '64px',
        background: 'rgba(15, 17, 23, 0.92)',
        backdropFilter: 'blur(12px)',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
      }}>
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '10px', textDecoration: 'none', color: '#fff', fontWeight: 800, fontSize: '18px' }}>
          <Image src="/logos/logo-white.svg" alt="ProMarshal" width={32} height={32} style={{ objectFit: 'contain' }} />
          ProMarshal
        </Link>
        <div style={{ display: 'flex', gap: 24, alignItems: 'center' }}>
          <Link href="/privacy" style={{ color: 'rgba(148,163,184,0.8)', fontSize: 14, textDecoration: 'none' }}>Privacy Policy</Link>
          <Link href="/terms" style={{ color: 'rgba(148,163,184,0.8)', fontSize: 14, textDecoration: 'none' }}>Terms of Service</Link>
          <Link href="/dpa" style={{ color: 'rgba(148,163,184,0.8)', fontSize: 14, textDecoration: 'none' }}>DPA</Link>
          <Link href="/signup" style={{
            background: '#78a530', color: '#fff', padding: '8px 20px',
            borderRadius: 50, fontSize: 14, fontWeight: 500, textDecoration: 'none',
          }}>Get Started</Link>
        </div>
      </nav>

      {/* Content */}
      <main style={{ paddingTop: 64 }}>
        {children}
      </main>

      {/* Footer */}
      <footer style={{
        borderTop: '1px solid rgba(255,255,255,0.08)',
        padding: '32px 40px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: 16,
      }}>
        <p style={{ color: '#64748b', fontSize: 14, margin: 0 }}>© 2026 ProMarshal. All rights reserved.</p>
        <div style={{ display: 'flex', gap: 24 }}>
          <Link href="/privacy" style={{ color: '#64748b', fontSize: 14, textDecoration: 'none' }}>Privacy Policy</Link>
          <Link href="/terms" style={{ color: '#64748b', fontSize: 14, textDecoration: 'none' }}>Terms of Service</Link>
          <Link href="/dpa" style={{ color: '#64748b', fontSize: 14, textDecoration: 'none' }}>DPA</Link>
          <a href="mailto:legal@promarshal.ai" style={{ color: '#64748b', fontSize: 14, textDecoration: 'none' }}>legal@promarshal.ai</a>
        </div>
      </footer>
    </div>
  )
}
