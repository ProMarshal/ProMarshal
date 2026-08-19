'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'

export function CookieNotice() {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const dismissed = localStorage.getItem('promarshal_cookie_notice')
    if (!dismissed) setVisible(true)
  }, [])

  const dismiss = () => {
    localStorage.setItem('promarshal_cookie_notice', 'dismissed')
    setVisible(false)
  }

  if (!visible) return null

  return (
    <div style={{
      position: 'fixed',
      bottom: 24,
      left: 24,
      zIndex: 9999,
      width: 260,
      background: '#161b27',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: 16,
      padding: '20px',
      display: 'flex',
      flexDirection: 'column',
      gap: 14,
      boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
    }}>
      <p style={{
        margin: 0,
        fontSize: 13,
        lineHeight: 1.7,
        color: '#94a3b8',
      }}>
        We use essential cookies to keep you signed in. No tracking, no ads.{' '}
        <Link href="/privacy#cookies" style={{ color: '#78a530', textDecoration: 'underline' }}>
          Privacy Policy
        </Link>
      </p>
      <button
        onClick={dismiss}
        style={{
          background: '#78a530',
          color: '#fff',
          border: 'none',
          borderRadius: 8,
          padding: '8px 0',
          fontSize: 13,
          fontWeight: 500,
          cursor: 'pointer',
          width: '100%',
        }}
      >
        Got it
      </button>
    </div>
  )
}
