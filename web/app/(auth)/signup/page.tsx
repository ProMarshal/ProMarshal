"use client"

import { Suspense, useEffect, useRef } from "react"
import Image from "next/image"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { signIn } from "next-auth/react"
import { EmailOTPForm } from "@/components/auth/email-otp-form"
import styles from "./signup.module.css"

function SignupContent() {
  const searchParams = useSearchParams()
  const callbackUrl = searchParams.get("callbackUrl") || "/dashboard"
  const hexSvgRef = useRef<SVGSVGElement>(null)
  const orbitRef = useRef<SVGSVGElement>(null)

  const handleGoogleSignIn = () => {
    signIn("google", { callbackUrl })
  }

  // Hex background generator — scattered hexagons with draw animation
  useEffect(() => {
    const svg = hexSvgRef.current
    if (!svg) return
    const NS = "http://www.w3.org/2000/svg"

    function getVertices(cx: number, cy: number, size: number) {
      const verts = []
      for (let i = 0; i < 6; i++) {
        const a = (Math.PI / 3) * i - Math.PI / 2
        verts.push({ x: cx + size * Math.cos(a), y: cy + size * Math.sin(a) })
      }
      return verts
    }

    function closestVertex(hex: { x: number; y: number; size: number }, tx: number, ty: number) {
      const verts = getVertices(hex.x, hex.y, hex.size)
      return verts.reduce((best, v) => {
        const d = Math.hypot(v.x - tx, v.y - ty)
        return d < best.d ? { v, d } : best
      }, { v: verts[0], d: Infinity }).v
    }

    function segmentsIntersect(x1: number, y1: number, x2: number, y2: number, x3: number, y3: number, x4: number, y4: number) {
      const d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
      if (Math.abs(d) < 0.0001) return false
      const t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d
      const u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / d
      return t >= 0 && t <= 1 && u >= 0 && u <= 1
    }

    function lineIntersectsHex(x1: number, y1: number, x2: number, y2: number, hex: { x: number; y: number; size: number }) {
      const verts = getVertices(hex.x, hex.y, hex.size)
      for (let i = 0; i < verts.length; i++) {
        const v1 = verts[i], v2 = verts[(i + 1) % verts.length]
        if (segmentsIntersect(x1, y1, x2, y2, v1.x, v1.y, v2.x, v2.y)) return true
      }
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2
      return Math.hypot(mx - hex.x, my - hex.y) < hex.size * 0.7
    }

    function generate() {
      svg!.innerHTML = ""
      const W = svg!.clientWidth || 600
      const H = svg!.clientHeight || 900
      const cxCenter = W / 2
      const cyCenter = H / 2
      const count = Math.floor((W * H) / 10000)
      const hexes: { x: number; y: number; size: number }[] = []

      let attempts = 0
      while (hexes.length < count && attempts < count * 50) {
        attempts++
        const h = { x: Math.random() * W, y: Math.random() * H, size: 15 + Math.random() * 25 }
        if (!hexes.some(e => Math.hypot(e.x - h.x, e.y - h.y) < e.size + h.size + 10)) hexes.push(h)
      }

      const pairs: [number, number][] = []
      for (let i = 0; i < hexes.length; i++) {
        for (let j = i + 1; j < hexes.length; j++) {
          if (Math.hypot(hexes[i].x - hexes[j].x, hexes[i].y - hexes[j].y) > 120) continue
          const v1 = closestVertex(hexes[i], hexes[j].x, hexes[j].y)
          const v2 = closestVertex(hexes[j], hexes[i].x, hexes[i].y)
          let ok = true
          for (let k = 0; k < hexes.length && ok; k++)
            if (k !== i && k !== j && lineIntersectsHex(v1.x, v1.y, v2.x, v2.y, hexes[k])) ok = false
          for (const [pi, pj] of pairs) {
            if (!ok) break
            const pv1 = closestVertex(hexes[pi], hexes[pj].x, hexes[pj].y)
            const pv2 = closestVertex(hexes[pj], hexes[pi].x, hexes[pi].y)
            if (segmentsIntersect(v1.x, v1.y, v2.x, v2.y, pv1.x, pv1.y, pv2.x, pv2.y)) ok = false
          }
          if (ok) pairs.push([i, j])
        }
      }

      const maxDist = Math.hypot(W / 2, H / 2)

      // Draw connector lines first (behind hexes)
      for (const [i, j] of pairs) {
        const v1 = closestVertex(hexes[i], hexes[j].x, hexes[j].y)
        const v2 = closestVertex(hexes[j], hexes[i].x, hexes[i].y)
        const len = Math.hypot(v2.x - v1.x, v2.y - v1.y)
        const midDist = Math.hypot((v1.x + v2.x) / 2 - cxCenter, (v1.y + v2.y) / 2 - cyCenter)
        const drawDelay = (midDist / maxDist) * 1.4
        const pulseDur = (4 + Math.random() * 4).toFixed(2)
        const pulseDelay = (drawDelay + 0.5 + Math.random() * 2).toFixed(2)
        const line = document.createElementNS(NS, "line")
        line.setAttribute("x1", String(v1.x))
        line.setAttribute("y1", String(v1.y))
        line.setAttribute("x2", String(v2.x))
        line.setAttribute("y2", String(v2.y))
        line.setAttribute("stroke", "rgba(120,165,48,0.18)")
        line.setAttribute("stroke-width", "1")
        line.setAttribute("stroke-dasharray", String(len))
        line.setAttribute("stroke-dashoffset", String(len))
        line.style.cssText = `animation:hex-draw 0.5s ease-out ${drawDelay.toFixed(2)}s forwards,hex-pulse ${pulseDur}s ease-in-out ${pulseDelay}s infinite`
        svg!.appendChild(line)
      }

      // Draw hexagons with independent pulse after draw-in
      for (const h of hexes) {
        const pts = getVertices(h.x, h.y, h.size).map(v => `${v.x},${v.y}`).join(" ")
        const perimeter = 6 * h.size
        const dist = Math.hypot(h.x - cxCenter, h.y - cyCenter)
        const drawDelay = (dist / maxDist) * 1.6
        const pulseDur = (5 + Math.random() * 4).toFixed(2)
        const pulseDelay = (drawDelay + 0.8 + Math.random() * 3).toFixed(2)
        const poly = document.createElementNS(NS, "polygon")
        poly.setAttribute("points", pts)
        poly.setAttribute("fill", "none")
        poly.setAttribute("stroke", "rgba(120,165,48,0.35)")
        poly.setAttribute("stroke-width", "1.2")
        poly.setAttribute("stroke-dasharray", String(perimeter))
        poly.setAttribute("stroke-dashoffset", String(perimeter))
        poly.style.cssText = `animation:hex-draw 0.8s ease-out ${drawDelay.toFixed(2)}s forwards,hex-pulse ${pulseDur}s ease-in-out ${pulseDelay}s infinite`
        svg!.appendChild(poly)
      }
    }

    generate()

    let lastW = svg.clientWidth, lastH = svg.clientHeight
    let resizeTimer: ReturnType<typeof setTimeout>
    const handleResize = () => {
      const w = svg!.clientWidth, h = svg!.clientHeight
      if (Math.abs(w - lastW) < 40 && Math.abs(h - lastH) < 120) return
      clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => { lastW = w; lastH = h; generate() }, 120)
    }
    window.addEventListener("resize", handleResize)
    return () => { window.removeEventListener("resize", handleResize); clearTimeout(resizeTimer) }
  }, [])

  // ── 3D Orbit Animation ──────────────────────────────────────────
  useEffect(() => {
    const svg = orbitRef.current
    if (!svg) return
    const NS = 'http://www.w3.org/2000/svg'
    const W = 460, H = 460, CX = W / 2, CY = H / 2, PERSP = 400

    const ORBITS = [
      {
        radius: 180, tiltX: 66, tiltZ: 0, speed: 0.35, nodes: [
          { a: 0, label: 'Action Agent', live: true },
          { a: 2 * Math.PI / 3, label: 'Reporting Agent', live: false },
          { a: 4 * Math.PI / 3, label: 'Forecasting Agent', live: false },
        ]
      },
      {
        radius: 180, tiltX: 66, tiltZ: 60, speed: -0.28, nodes: [
          { a: Math.PI / 3, label: 'Cadence Agent', live: true },
          { a: Math.PI, label: 'Intelligence Agent', live: false },
          { a: 5 * Math.PI / 3, label: 'Analytics Agent', live: false },
        ]
      },
      {
        radius: 180, tiltX: 66, tiltZ: 120, speed: 0.42, nodes: [
          { a: 2 * Math.PI / 3, label: 'Polling Agent', live: true },
          { a: 4 * Math.PI / 3, label: 'Analytics Agent', live: false },
          { a: 0, label: 'Reporting Agent', live: false },
        ]
      },
    ]

    svg.innerHTML = ''

    // Defs: glow filter
    const defs = document.createElementNS(NS, 'defs')
    const filter = document.createElementNS(NS, 'filter')
    filter.id = 'orbitGlow'
    filter.setAttribute('x', '-60%'); filter.setAttribute('y', '-60%')
    filter.setAttribute('width', '220%'); filter.setAttribute('height', '220%')
    const blur = document.createElementNS(NS, 'feGaussianBlur')
    blur.setAttribute('in', 'SourceGraphic'); blur.setAttribute('stdDeviation', '3'); blur.setAttribute('result', 'b')
    const merge = document.createElementNS(NS, 'feMerge')
      ;['b', 'SourceGraphic'].forEach(i => { const n = document.createElementNS(NS, 'feMergeNode'); n.setAttribute('in', i); merge.appendChild(n) })
    filter.appendChild(blur); filter.appendChild(merge); defs.appendChild(filter); svg.appendChild(defs)

    // Draw orbit path ellipses (computed from tilt parameters)
    for (const orb of ORBITS) {
      const tX = orb.tiltX * Math.PI / 180
      const tZ = orb.tiltZ * Math.PI / 180
      const pts: string[] = []
      for (let i = 0; i <= 120; i++) {
        const θ = (i / 120) * 2 * Math.PI
        const x0 = orb.radius * Math.cos(θ), y0 = orb.radius * Math.sin(θ)
        const x1 = x0, y1 = y0 * Math.cos(tX), z1 = y0 * Math.sin(tX)
        const x2 = x1 * Math.cos(tZ) - y1 * Math.sin(tZ)
        const y2 = x1 * Math.sin(tZ) + y1 * Math.cos(tZ)
        // Draw the path using purely orthographic projection (no perspective scaling to x/y)
        // so that the geometric center of the ellipse is exactly at CX, CY.
        pts.push(`${i === 0 ? 'M' : 'L'}${(CX + x2).toFixed(1)},${(CY + y2).toFixed(1)}`)
      }
      const path = document.createElementNS(NS, 'path')
      path.setAttribute('d', pts.join(' ') + 'Z')
      path.setAttribute('fill', 'none')
      path.setAttribute('stroke', 'rgba(120,165,48,0.14)')
      path.setAttribute('stroke-width', '1')
      svg.appendChild(path)
    }

    // Build animated node elements
    type NodeEl = { dot: SVGCircleElement; label: SVGTextElement; orb: typeof ORBITS[0]; node: typeof ORBITS[0]['nodes'][0]; angle: number }
    const nodeEls: NodeEl[] = []
    for (const orb of ORBITS) {
      for (const node of orb.nodes) {
        const dot = document.createElementNS(NS, 'circle')
        dot.setAttribute('fill', '#78a530')
        dot.setAttribute('filter', 'url(#orbitGlow)')
        const label = document.createElementNS(NS, 'text')
        label.setAttribute('text-anchor', 'middle')
        label.setAttribute('font-family', 'system-ui, -apple-system, sans-serif')
        label.setAttribute('font-weight', '600')
        label.setAttribute('fill', 'rgba(240,244,255,0.92)')
        label.setAttribute('pointer-events', 'none')
        label.textContent = node.label
        svg.appendChild(dot)
        svg.appendChild(label)
        nodeEls.push({ dot, label, orb, node, angle: node.a })
      }
    }

    let raf: number, last = 0
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05)
      last = now
      for (const el of nodeEls) {
        el.angle += el.orb.speed * dt
        const tX = el.orb.tiltX * Math.PI / 180
        const tZ = el.orb.tiltZ * Math.PI / 180
        const r = el.orb.radius, θ = el.angle
        const x0 = r * Math.cos(θ), y0 = r * Math.sin(θ)
        const x1 = x0, y1 = y0 * Math.cos(tX), z1 = y0 * Math.sin(tX)
        const x2 = x1 * Math.cos(tZ) - y1 * Math.sin(tZ)
        const y2 = x1 * Math.sin(tZ) + y1 * Math.cos(tZ), z2 = z1
        // Position orthographically so it perfectly aligns with the drawn ellipse path
        const sx = CX + x2, sy = CY + y2
        // Use perspective only for scale (size) and depth opacity
        const sc = Math.max(0.25, PERSP / (PERSP + z2))
        const depthOp = Math.max(0.05, 0.5 + ((-z2) / r) * 0.5) // front=1, back=0
        const dotR = Math.max(2.5, 7 * sc)
        el.dot.setAttribute('cx', sx.toFixed(1))
        el.dot.setAttribute('cy', sy.toFixed(1))
        el.dot.setAttribute('r', dotR.toFixed(1))
        el.dot.setAttribute('opacity', depthOp.toFixed(2))
        const fontSize = Math.max(9, 15 * Math.sqrt(sc)) // slightly softer scaling for text
        const labelOp = z2 < 10 ? depthOp : depthOp * 0.25 // fade label heavily when behind
        el.label.setAttribute('x', sx.toFixed(1))
        el.label.setAttribute('y', (sy - dotR - 5).toFixed(1))
        el.label.setAttribute('font-size', fontSize.toFixed(1))
        el.label.setAttribute('opacity', labelOp.toFixed(2))
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(t => { last = t; raf = requestAnimationFrame(tick) })
    return () => cancelAnimationFrame(raf)
  }, [])

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>

      {/* ── Left dark panel ── */}
      <div className={`${styles.leftPanel} hidden lg:flex`}>

        {/* Hex background */}
        <div className={styles.hexBg}>
          <svg ref={hexSvgRef} xmlns="http://www.w3.org/2000/svg" style={{ width: "100%", height: "100%" }} />
        </div>

        {/* 3D Orbit Stage */}
        <div style={{ position: 'relative', zIndex: 1, width: '460px', height: '460px' }}>

          {/* Logo hub — center */}
          <div style={{
            position: 'absolute', top: '50%', left: '50%',
            transform: 'translate(-50%, -50%)',
            width: '100px', height: '100px',
            borderRadius: '50%',
            background: 'rgba(120,165,48,0.15)',
            border: '2px solid rgba(120,165,48,0.55)',
            boxShadow: '0 0 32px rgba(120,165,48,0.3)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 5,
          }}>
            <img src="/logos/logo-white.svg" alt="ProMarshal" style={{ width: '64px', height: '64px', objectFit: 'contain' }} />
          </div>

          {/* Animated orbit SVG */}
          <svg ref={orbitRef} width="460" height="460" style={{ position: 'absolute', top: 0, left: 0 }} />

        </div>


      </div>

      {/* ── Right white panel ── */}
      <div className={styles.rightPanel}>
        <div className={styles.formWrapper}>

          {/* Logo */}
          <Link href="https://promarshal.ai" className={styles.formLogo}>
            <Image
              src="/logos/logo-black.svg"
              alt="ProMarshal"
              width={32}
              height={32}
              style={{ objectFit: "contain" }}
            />
            <span className={styles.formLogoText}>ProMarshal</span>
          </Link>

          <h1 className={styles.formHeading}>Get started for free</h1>
          <p className={styles.formSub}>Sign in or create your account to continue</p>

          {/* Google */}
          <button type="button" onClick={handleGoogleSignIn} className={styles.googleBtn}>
            <svg width="18" height="18" viewBox="0 0 24 24">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
            </svg>
            Continue with Google
          </button>

          {/* Divider */}
          <div className={styles.divider}>
            <div className={styles.dividerLine} />
            <span className={styles.dividerText}>or</span>
            <div className={styles.dividerLine} />
          </div>

          {/* Email OTP — untouched */}
          <EmailOTPForm callbackUrl={callbackUrl} />

          {/* Terms */}
          <p className={styles.terms}>
            By continuing, you agree to our{" "}
            <Link href="/terms">Terms of Service</Link>
            {" "}and{" "}
            <Link href="/privacy">Privacy Policy</Link>
          </p>

        </div>
      </div>

    </div>
  )
}

export default function SignupPage() {
  return (
    <Suspense fallback={
      <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center" }}>
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-[#78a530]" />
      </div>
    }>
      <SignupContent />
    </Suspense>
  )
}
