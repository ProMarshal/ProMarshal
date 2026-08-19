"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import * as THREE from "three"

// ── Types ────────────────────────────────────────────────────────────────────

type NodeType = "project" | "goal" | "scope" | "requirement" | "feature" | "workitem" | "risk" | "blocker" | "decision" | "actionitem"

interface GraphNode {
    id: string
    name: string
    type: NodeType
    val?: number
}

interface GraphLink {
    source: string
    target: string
}

// ── Palette ──────────────────────────────────────────────────────────────────

const NODE_COLORS: Record<NodeType, string> = {
    project:     "#ffffff",
    goal:        "#78a530",
    scope:       "#78a530",
    requirement: "#78a530",
    feature:     "#2563eb",
    workitem:    "#06b6d4",
    risk:        "#f59e0b",
    blocker:     "#ef4444",
    decision:    "#a855f7",
    actionitem:  "#f97316",
}

// Short prefix shown above the node name
const TYPE_PREFIX: Partial<Record<NodeType, string>> = {
    goal:        "Goal",
    scope:       "Scope",
    requirement: "Requirement",
    feature:     "Feature",
    workitem:    "Work Item",
    risk:        "Risk",
    blocker:     "Blocker",
    decision:    "Decision",
    actionitem:  "Action Item",
}

// ── Sample data ──────────────────────────────────────────────────────────────

const ALL_NODES: GraphNode[] = [
    { id: "proj", name: "Customer Portal App",    type: "project",     val: 14 },
    { id: "goal", name: "Reduce support load 40%",type: "goal",        val: 9  },
    { id: "scope",name: "Web + Mobile MVP",       type: "scope",       val: 9  },
    { id: "req",  name: "Requirements Defined",   type: "requirement", val: 9  },
    { id: "f1",   name: "User Auth Module",       type: "feature",     val: 7  },
    { id: "f2",   name: "Dashboard Redesign",     type: "feature",     val: 7  },
    { id: "f3",   name: "Payment Integration",    type: "feature",     val: 7  },
    { id: "w1",   name: "OAuth Login Flow",       type: "workitem",    val: 4  },
    { id: "w2",   name: "Token Refresh Logic",    type: "workitem",    val: 4  },
    { id: "w3",   name: "Chart Components",       type: "workitem",    val: 4  },
    { id: "w4",   name: "Dark Mode Support",      type: "workitem",    val: 4  },
    { id: "w5",   name: "PDF Export",             type: "workitem",    val: 4  },
    { id: "w6",   name: "Payment Gateway API",    type: "workitem",    val: 4  },
    { id: "w7",   name: "Webhook Handlers",       type: "workitem",    val: 4  },
    { id: "r1",   name: "Scope Creep Sprint 4",   type: "risk",        val: 6  },
    { id: "r2",   name: "Resource Overload",      type: "risk",        val: 6  },
    { id: "b1",   name: "Vendor Creds Invalid",   type: "blocker",     val: 6  },
    { id: "b2",   name: "Staging URL Down",       type: "blocker",     val: 6  },
    { id: "d1",   name: "PDF export prioritised", type: "decision",    val: 5  },
    { id: "d2",   name: "Notif. system delayed",  type: "decision",    val: 5  },
    { id: "a1",   name: "Escalate vendor issue",  type: "actionitem",  val: 4  },
    { id: "a2",   name: "Share sprint plan",      type: "actionitem",  val: 4  },
    { id: "a3",   name: "Send timeline doc",      type: "actionitem",  val: 4  },
]

const ALL_LINKS: GraphLink[] = [
    { source: "proj", target: "goal"  },
    { source: "proj", target: "scope" },
    { source: "proj", target: "req"   },
    { source: "req",  target: "f1"   },
    { source: "req",  target: "f2"   },
    { source: "req",  target: "f3"   },
    { source: "f1",   target: "w1"   },
    { source: "f1",   target: "w2"   },
    { source: "f2",   target: "w3"   },
    { source: "f2",   target: "w4"   },
    { source: "f2",   target: "w5"   },
    { source: "f3",   target: "w6"   },
    { source: "f3",   target: "w7"   },
    { source: "w6",   target: "r1"   },
    { source: "w1",   target: "r2"   },
    { source: "w6",   target: "b1"   },
    { source: "w3",   target: "b2"   },
    { source: "w5",   target: "d1"   },
    { source: "f2",   target: "d2"   },
    { source: "b1",   target: "a1"   },
    { source: "d1",   target: "a2"   },
    { source: "r1",   target: "a3"   },
]

// ── Filter options ────────────────────────────────────────────────────────────

const FILTER_OPTIONS: { key: NodeType | "all"; label: string; color: string }[] = [
    { key: "all",        label: "All",          color: "#6b7280"                },
    { key: "feature",    label: "Features",     color: NODE_COLORS.feature      },
    { key: "workitem",   label: "Work Items",   color: NODE_COLORS.workitem     },
    { key: "risk",       label: "Risks",        color: NODE_COLORS.risk         },
    { key: "blocker",    label: "Blockers",     color: NODE_COLORS.blocker      },
    { key: "decision",   label: "Decisions",    color: NODE_COLORS.decision     },
    { key: "actionitem", label: "Action Items", color: NODE_COLORS.actionitem   },
]

const LEGEND_ITEMS: { type: NodeType; label: string }[] = [
    { type: "goal",        label: "Goal / Scope / Req" },
    { type: "feature",     label: "Feature"             },
    { type: "workitem",    label: "Work Item"            },
    { type: "risk",        label: "Risk"                 },
    { type: "blocker",     label: "Blocker"              },
    { type: "decision",    label: "Decision"             },
    { type: "actionitem",  label: "Action Item"          },
]

// ── Label sprite builder ──────────────────────────────────────────────────────

function buildLabelSprite(node: GraphNode): THREE.Sprite {
    const prefix  = TYPE_PREFIX[node.type] ?? ""
    const color   = NODE_COLORS[node.type] ?? "#ffffff"
    const hasPrefix = prefix.length > 0

    const W = 320
    const H = hasPrefix ? 80 : 52
    const canvas = document.createElement("canvas")
    canvas.width  = W
    canvas.height = H
    const ctx = canvas.getContext("2d")!

    if (hasPrefix) {
        // Prefix label (e.g. "Blocker:")
        ctx.font = "bold 22px Inter, system-ui, sans-serif"
        ctx.fillStyle = color
        ctx.textAlign = "center"
        ctx.textBaseline = "middle"
        ctx.fillText(prefix + ":", W / 2, 22)

        // Node name
        ctx.font = "20px Inter, system-ui, sans-serif"
        ctx.fillStyle = "rgba(255,255,255,0.90)"
        ctx.textAlign = "center"
        ctx.textBaseline = "middle"
        ctx.fillText(node.name, W / 2, 56)
    } else {
        // Project root — just name, larger
        ctx.font = "bold 26px Inter, system-ui, sans-serif"
        ctx.fillStyle = "#ffffff"
        ctx.textAlign = "center"
        ctx.textBaseline = "middle"
        ctx.fillText(node.name, W / 2, H / 2)
    }

    const texture = new THREE.CanvasTexture(canvas)
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false })
    const sprite = new THREE.Sprite(mat)
    const scale = 28
    sprite.scale.set(scale, scale * (H / W), 1)
    // Position label above the sphere
    const radius = Math.cbrt((node.val ?? 4) * 4 / Math.PI) * 2.5
    sprite.position.set(0, radius + (hasPrefix ? 9 : 7), 0)
    return sprite
}

// ── Glow rings for risk / blocker ─────────────────────────────────────────────

function buildGlowMesh(node: GraphNode): THREE.Group {
    const group = new THREE.Group()
    const color = new THREE.Color(NODE_COLORS[node.type])
    const baseR = Math.cbrt((node.val ?? 6) * 4 / Math.PI) * 2.5

    const rings = [
        { mult: 2.2, opacity: 0.22 },
        { mult: 3.2, opacity: 0.10 },
        { mult: 4.4, opacity: 0.05 },
    ]

    rings.forEach(({ mult, opacity }) => {
        const geo = new THREE.SphereGeometry(baseR * mult, 20, 20)
        const mat = new THREE.MeshBasicMaterial({
            color,
            transparent: true,
            opacity,
            side: THREE.BackSide,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
        })
        group.add(new THREE.Mesh(geo, mat))
    })

    return group
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function ForecastMindMap({ isDarkMode }: { isDarkMode: boolean }) {
    const containerRef = useRef<HTMLDivElement>(null)
    const graphRef     = useRef<any>(null)
    const [activeFilter,  setActiveFilter]  = useState<NodeType | "all">("all")
    const [ForceGraph3D,  setForceGraph3D]  = useState<any>(null)
    const [dims,          setDims]          = useState({ width: 800, height: 520 })

    useEffect(() => {
        import("react-force-graph-3d").then((mod) => setForceGraph3D(() => mod.default))
    }, [])

    useEffect(() => {
        if (!containerRef.current) return
        const ro = new ResizeObserver((entries) => {
            const { width, height } = entries[0].contentRect
            setDims({ width, height })
        })
        ro.observe(containerRef.current)
        return () => ro.disconnect()
    }, [])

    // Filter: selected type + full ancestor chain up to root + direct children
    const graphData = useCallback(() => {
        if (activeFilter === "all") return { nodes: ALL_NODES, links: ALL_LINKS }

        const primaryIds = new Set(ALL_NODES.filter(n => n.type === activeFilter).map(n => n.id))

        const parents: Record<string, string[]> = {}
        ALL_LINKS.forEach(l => {
            const s = typeof l.source === "object" ? (l.source as any).id : l.source
            const t = typeof l.target === "object" ? (l.target as any).id : l.target
            if (!parents[t]) parents[t] = []
            parents[t].push(s)
        })

        const ancestorIds = new Set<string>()
        const queue = [...primaryIds]
        while (queue.length) {
            const cur = queue.shift()!
            ;(parents[cur] || []).forEach(p => {
                if (!ancestorIds.has(p)) { ancestorIds.add(p); queue.push(p) }
            })
        }

        const childIds = new Set<string>()
        ALL_LINKS.forEach(l => {
            const s = typeof l.source === "object" ? (l.source as any).id : l.source
            const t = typeof l.target === "object" ? (l.target as any).id : l.target
            if (primaryIds.has(s)) childIds.add(t)
        })

        const visibleIds = new Set([...primaryIds, ...ancestorIds, ...childIds])
        return {
            nodes: ALL_NODES.filter(n => visibleIds.has(n.id)),
            links: ALL_LINKS.filter(l => {
                const s = typeof l.source === "object" ? (l.source as any).id : l.source
                const t = typeof l.target === "object" ? (l.target as any).id : l.target
                return visibleIds.has(s) && visibleIds.has(t)
            }),
        }
    }, [activeFilter])

    const nodeColor = useCallback((node: any) => {
        const color = NODE_COLORS[node.type as NodeType] ?? "#6b7280"
        if (activeFilter === "all") return color
        const alwaysBright: NodeType[] = ["project", "goal", "scope", "requirement"]
        if (alwaysBright.includes(node.type) || node.type === activeFilter) return color
        return isDarkMode ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.08)"
    }, [activeFilter, isDarkMode])

    // Custom 3-D object: label sprite + glow rings for risk/blocker
    const nodeThreeObject = useCallback((node: any) => {
        const group = new THREE.Group()
        group.add(buildLabelSprite(node as GraphNode))
        if (node.type === "risk" || node.type === "blocker") {
            group.add(buildGlowMesh(node as GraphNode))
        }
        return group
    }, [])

    const bg        = isDarkMode ? "#080d1a" : "#f8fafc"
    const linkColor = isDarkMode ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.15)"

    return (
        <div className="flex flex-col gap-3">

            {/* Filter bar */}
            <div className="flex items-center gap-2 flex-wrap">
                {FILTER_OPTIONS.map(opt => (
                    <button
                        key={opt.key}
                        type="button"
                        onClick={() => setActiveFilter(opt.key as NodeType | "all")}
                        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border transition ${
                            activeFilter === opt.key
                                ? "text-white border-transparent shadow-sm"
                                : isDarkMode
                                    ? "border-white/[0.10] text-gray-400 hover:border-white/20 hover:text-gray-200"
                                    : "border-gray-200 text-gray-500 hover:border-gray-300 hover:text-gray-700"
                        }`}
                        style={activeFilter === opt.key ? { background: opt.color } : {}}
                    >
                        <span
                            className="w-2 h-2 rounded-full flex-shrink-0"
                            style={{ background: activeFilter === opt.key ? "rgba(255,255,255,0.6)" : opt.color }}
                        />
                        {opt.label}
                    </button>
                ))}
            </div>

            {/* Graph + legend */}
            <div className="flex gap-3 items-start">

                {/* Graph */}
                <div
                    ref={containerRef}
                    className="relative rounded-xl overflow-hidden flex-1 min-w-0"
                    style={{
                        height: 540,
                        background: bg,
                        border: isDarkMode ? "1px solid rgba(255,255,255,0.08)" : "1px solid #e5e7eb",
                    }}
                >
                    {ForceGraph3D ? (
                        <ForceGraph3D
                            ref={graphRef}
                            width={dims.width}
                            height={dims.height}
                            backgroundColor={bg}
                            graphData={graphData()}
                            nodeLabel={(n: any) => TYPE_PREFIX[n.type as NodeType] ? `${TYPE_PREFIX[n.type as NodeType]}: ${n.name}` : n.name}
                            nodeColor={nodeColor}
                            nodeVal={(n: any) => n.val ?? 4}
                            nodeOpacity={0.92}
                            nodeThreeObjectExtend={true}
                            nodeThreeObject={nodeThreeObject}
                            linkColor={() => linkColor}
                            linkWidth={1.2}
                            linkOpacity={0.55}
                            enableNodeDrag
                            enableNavigationControls
                            showNavInfo={false}
                        />
                    ) : (
                        <div className="absolute inset-0 flex items-center justify-center">
                            <span className="text-sm text-gray-400">Loading graph…</span>
                        </div>
                    )}

                    <div className="absolute top-3 left-3">
                        <span
                            className="text-xs text-gray-500 px-2 py-1 rounded-md"
                            style={{ background: isDarkMode ? "rgba(8,13,26,0.72)" : "rgba(255,255,255,0.82)" }}
                        >
                            Left-drag: rotate &nbsp;&middot;&nbsp; Right-drag: pan &nbsp;&middot;&nbsp; Scroll: zoom
                        </span>
                    </div>
                </div>

                {/* Legend — outside the canvas so it's never clipped */}
                <div
                    className="flex-shrink-0 rounded-xl px-4 py-3 flex flex-col gap-2.5"
                    style={{
                        background: isDarkMode ? "rgba(13,18,37,0.95)" : "rgba(255,255,255,0.95)",
                        border: isDarkMode ? "1px solid rgba(255,255,255,0.08)" : "1px solid #e5e7eb",
                        minWidth: 156,
                    }}
                >
                    <p className={`text-xs font-semibold uppercase tracking-wide mb-0.5 ${isDarkMode ? "text-gray-500" : "text-gray-400"}`}>
                        Legend
                    </p>
                    <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-full flex-shrink-0 border border-gray-400" style={{ background: NODE_COLORS.project }} />
                        <span className={`text-xs ${isDarkMode ? "text-gray-400" : "text-gray-600"}`}>Project</span>
                    </div>
                    {LEGEND_ITEMS.map(item => (
                        <div key={item.type} className="flex items-center gap-2">
                            <span
                                className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                                style={{
                                    background: NODE_COLORS[item.type],
                                    boxShadow: (item.type === "risk" || item.type === "blocker")
                                        ? `0 0 6px 2px ${NODE_COLORS[item.type]}88`
                                        : undefined,
                                }}
                            />
                            <span className={`text-xs ${isDarkMode ? "text-gray-400" : "text-gray-600"}`}>{item.label}</span>
                        </div>
                    ))}
                    <div className={`mt-1 pt-2 border-t text-xs ${isDarkMode ? "border-white/[0.06] text-gray-600" : "border-gray-100 text-gray-400"}`}>
                        Glowing = critical
                    </div>
                </div>

            </div>
        </div>
    )
}
