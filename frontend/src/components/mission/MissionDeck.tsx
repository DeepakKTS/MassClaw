"use client";

import { useWorkflows } from "@/hooks/useWorkflows";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { truncate } from "@/lib/utils";
import { Loader2 } from "lucide-react";
import { useMemo, useState, useRef, useEffect, useCallback } from "react";

const STATUS = {
  completed:   { fill: "#50C878", stroke: "#50C87880", glow: "rgba(80,200,120,0.18)" },
  failed:      { fill: "#E05555", stroke: "#E0555580", glow: "rgba(224,85,85,0.15)" },
  cancelled:   { fill: "#6B6B74", stroke: "#6B6B7480", glow: "rgba(107,107,116,0.1)" },
  pending:     { fill: "#E08A3E", stroke: "#E08A3E80", glow: "rgba(224,138,62,0.22)" },
  decomposing: { fill: "#E08A3E", stroke: "#E08A3E80", glow: "rgba(224,138,62,0.22)" },
  running:     { fill: "#E08A3E", stroke: "#E08A3E80", glow: "rgba(224,138,62,0.28)" },
} as const;

function getStatus(s: string) { return STATUS[s as keyof typeof STATUS] || STATUS.completed; }

const PEAK_SPACING = 80; // px between each mission peak
const H = 220;
const BASELINE = H * 0.55;
const EDGE_PAD = 60;

export function MissionDeck() {
  const { data, isLoading } = useWorkflows();
  const router = useRouter();
  const missions = data?.items ?? [];
  const scrollRef = useRef<HTMLDivElement>(null);
  const [containerW, setContainerW] = useState(800);
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  // Drag-to-scroll state
  const isDragging = useRef(false);
  const dragStartX = useRef(0);
  const scrollStartX = useRef(0);

  // Measure container
  useEffect(() => {
    const el = scrollRef.current?.parentElement;
    if (!el) return;
    const obs = new ResizeObserver((entries) => setContainerW(Math.max(entries[0].contentRect.width, 400)));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Ambient tick
  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 60);
    return () => clearInterval(interval);
  }, []);

  // Auto-scroll to the right (latest) on mount and when missions change
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
    }
  }, [missions.length]);

  // Drag handlers
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    isDragging.current = true;
    dragStartX.current = e.clientX;
    scrollStartX.current = scrollRef.current?.scrollLeft ?? 0;
    scrollRef.current?.setPointerCapture(e.pointerId);
    if (scrollRef.current) scrollRef.current.style.cursor = "grabbing";
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!isDragging.current || !scrollRef.current) return;
    const dx = e.clientX - dragStartX.current;
    scrollRef.current.scrollLeft = scrollStartX.current - dx;
  }, []);

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    isDragging.current = false;
    scrollRef.current?.releasePointerCapture(e.pointerId);
    if (scrollRef.current) scrollRef.current.style.cursor = "grab";
  }, []);

  // SVG total width — grows with missions, minimum = container width
  const sorted = useMemo(() =>
    [...missions].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    [missions]
  );
  const svgWidth = Math.max(containerW, sorted.length * PEAK_SPACING + EDGE_PAD * 2 + 60);
  const NOW_X = svgWidth - EDGE_PAD;

  // Wave data
  const waveData = useMemo(() => {
    return sorted.map((m, i) => {
      const x = EDGE_PAD + i * PEAK_SPACING;
      const budgetRatio = Math.min(m.budget_limit / 1000, 1);
      const peakHeight = 30 + budgetRatio * 60;
      const isActive = ["pending", "decomposing", "running"].includes(m.status);
      return { x, peakHeight, mission: m, isActive, status: getStatus(m.status) };
    });
  }, [sorted]);

  // Waveform path
  const wavePath = useMemo(() => {
    if (waveData.length === 0) return "";
    const points: { x: number; y: number }[] = [{ x: EDGE_PAD - 30, y: BASELINE }];
    for (const d of waveData) {
      points.push({ x: d.x - 14, y: BASELINE + 4 });
      points.push({ x: d.x, y: BASELINE - d.peakHeight });
      points.push({ x: d.x + 14, y: BASELINE + 4 });
    }
    points.push({ x: NOW_X + 10, y: BASELINE });
    let path = `M ${points[0].x} ${points[0].y}`;
    for (let i = 1; i < points.length; i++) {
      const prev = points[i - 1];
      const curr = points[i];
      const cpx = (prev.x + curr.x) / 2;
      path += ` C ${cpx} ${prev.y}, ${cpx} ${curr.y}, ${curr.x} ${curr.y}`;
    }
    return path;
  }, [waveData, NOW_X]);

  // Ambient wave
  const ambientPath = useMemo(() => {
    const pts: string[] = [];
    for (let x = EDGE_PAD - 30; x <= NOW_X + 10; x += 8) {
      const y = BASELINE + Math.sin(x * 0.025 + tick * 0.04) * 5 + Math.sin(x * 0.06 + tick * 0.025) * 3;
      pts.push(`${x},${y}`);
    }
    return `M ${pts.join(" L ")}`;
  }, [tick, NOW_X]);

  if (isLoading) {
    return <div className="flex items-center justify-center h-full"><Loader2 size={18} className="animate-spin text-massclaw-text-muted/20" /></div>;
  }

  if (missions.length === 0) {
    return <div className="flex items-center justify-center h-full"><p className="text-massclaw-text-muted/25 text-sm">Your command timeline is empty</p></div>;
  }

  const hoveredData = hoveredIdx !== null ? waveData[hoveredIdx] : null;

  return (
    <div className="relative w-full h-full min-h-[220px]">
      {/* Scrollable container — invisible scrollbar, drag to scroll */}
      <div
        ref={scrollRef}
        className="w-full h-full overflow-x-auto overflow-y-hidden cursor-grab select-none"
        style={{ scrollbarWidth: "none", msOverflowStyle: "none", WebkitOverflowScrolling: "touch" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onMouseLeave={() => setHoveredIdx(null)}
      >
        {/* Hide webkit scrollbar */}
        <style>{`div[class*="overflow-x-auto"]::-webkit-scrollbar { display: none; }`}</style>

        <svg width={svgWidth} height={H} viewBox={`0 0 ${svgWidth} ${H}`} className="block">
          <defs>
            <linearGradient id="wf-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(224,138,62,0.20)" />
              <stop offset="50%" stopColor="rgba(224,138,62,0.06)" />
              <stop offset="100%" stopColor="rgba(224,138,62,0)" />
            </linearGradient>
            <linearGradient id="wf-mirror" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(224,138,62,0.08)" />
              <stop offset="100%" stopColor="rgba(224,138,62,0)" />
            </linearGradient>
            <filter id="pk-glow" x="-100%" y="-100%" width="300%" height="300%">
              <feGaussianBlur stdDeviation="6" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <linearGradient id="pk-glass" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(255,255,255,0.35)" />
              <stop offset="30%" stopColor="rgba(255,255,255,0.10)" />
              <stop offset="100%" stopColor="rgba(255,255,255,0)" />
            </linearGradient>
            <filter id="wf-line-glow" x="-10%" y="-50%" width="120%" height="200%">
              <feGaussianBlur stdDeviation="2" />
            </filter>
          </defs>

          {/* Ambient wave */}
          <path d={ambientPath} fill="none" stroke="rgba(224,138,62,0.04)" strokeWidth="1.5" />

          {/* Fill */}
          {wavePath && <path d={`${wavePath} L ${NOW_X + 10} ${BASELINE} L ${EDGE_PAD - 30} ${BASELINE} Z`} fill="url(#wf-fill)" />}

          {/* Mirror */}
          {wavePath && (
            <g opacity="0.25" transform={`translate(0, ${BASELINE * 2}) scale(1, -1)`}>
              <path d={`${wavePath} L ${NOW_X + 10} ${BASELINE} L ${EDGE_PAD - 30} ${BASELINE} Z`} fill="url(#wf-mirror)" />
              <path d={wavePath} fill="none" stroke="rgba(224,138,62,0.12)" strokeWidth="1" />
            </g>
          )}

          {/* Line glow */}
          {wavePath && <path d={wavePath} fill="none" stroke="rgba(224,138,62,0.15)" strokeWidth="4" strokeLinecap="round" filter="url(#wf-line-glow)" />}

          {/* Main line */}
          {wavePath && <path d={wavePath} fill="none" stroke="rgba(224,138,62,0.45)" strokeWidth="1.5" strokeLinecap="round" />}

          {/* Baseline */}
          <line x1={EDGE_PAD - 30} y1={BASELINE} x2={NOW_X + 10} y2={BASELINE} stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
          <line x1={EDGE_PAD - 30} y1={BASELINE + 1} x2={NOW_X + 10} y2={BASELINE + 1} stroke="rgba(255,255,255,0.015)" strokeWidth="1" />

          {/* Peaks */}
          {waveData.map((d, i) => {
            const peakY = BASELINE - d.peakHeight;
            const isHovered = hoveredIdx === i;
            const nr = isHovered ? 8 : d.isActive ? 6 : 5;

            return (
              <g key={d.mission.workflow_id} className="cursor-pointer"
                onMouseEnter={() => { if (!isDragging.current) setHoveredIdx(i); }}
                onClick={() => { if (!isDragging.current) router.push(`/missions/${d.mission.workflow_id}`); }}>

                <line x1={d.x} y1={BASELINE} x2={d.x} y2={peakY + nr} stroke={isHovered ? d.status.fill : d.status.stroke} strokeWidth={isHovered ? 1.5 : 0.7} />
                <line x1={d.x} y1={BASELINE} x2={d.x} y2={BASELINE + (d.peakHeight * 0.3)} stroke={d.status.stroke} strokeWidth="0.4" opacity="0.2" />

                {d.isActive && (
                  <>
                    <circle cx={d.x} cy={peakY} r="18" fill="none" stroke={d.status.fill} strokeWidth="0.6" opacity="0.2">
                      <animate attributeName="r" values="8;22;8" dur="3s" repeatCount="indefinite" />
                      <animate attributeName="opacity" values="0.25;0;0.25" dur="3s" repeatCount="indefinite" />
                    </circle>
                    <circle cx={d.x} cy={peakY} r="12" fill="none" stroke={d.status.fill} strokeWidth="0.5" opacity="0.15">
                      <animate attributeName="r" values="6;16;6" dur="2s" repeatCount="indefinite" begin="0.5s" />
                      <animate attributeName="opacity" values="0.2;0;0.2" dur="2s" repeatCount="indefinite" begin="0.5s" />
                    </circle>
                  </>
                )}

                <circle cx={d.x} cy={peakY} r={isHovered ? 14 : 8} fill={d.status.glow} filter="url(#pk-glow)" />
                <circle cx={d.x} cy={peakY} r={nr} fill="var(--mc-surface)" stroke={d.status.fill} strokeWidth={isHovered ? 2 : 1.5} />
                <ellipse cx={d.x} cy={peakY - nr * 0.3} rx={nr * 0.6} ry={nr * 0.35} fill="url(#pk-glass)" />
                <circle cx={d.x} cy={peakY} r={isHovered ? 3 : 2.5} fill={d.status.fill} opacity={d.isActive ? 1 : 0.7}>
                  {d.isActive && <animate attributeName="opacity" values="0.4;1;0.4" dur="1.5s" repeatCount="indefinite" />}
                </circle>
                <circle cx={d.x} cy={BASELINE + (BASELINE - peakY) * 0.25} r={nr * 0.5} fill={d.status.fill} opacity="0.06" />
              </g>
            );
          })}

          {/* NOW */}
          <g>
            <line x1={NOW_X} y1={BASELINE - 35} x2={NOW_X} y2={BASELINE + 20} stroke="rgba(224,138,62,0.12)" strokeWidth="1" strokeDasharray="3 4" />
            <circle cx={NOW_X} cy={BASELINE} r="3.5" fill="#E08A3E" opacity="0.7">
              <animate attributeName="r" values="2.5;4.5;2.5" dur="2s" repeatCount="indefinite" />
              <animate attributeName="opacity" values="0.4;0.9;0.4" dur="2s" repeatCount="indefinite" />
            </circle>
            <ellipse cx={NOW_X} cy={BASELINE - 1.5} rx="2" ry="1.2" fill="rgba(255,255,255,0.2)" />
            <text x={NOW_X} y={BASELINE + 32} textAnchor="middle" fill="rgba(224,138,62,0.3)" fontSize="8" fontFamily="var(--font-geist-sans)" fontWeight="500">now</text>
          </g>
        </svg>
      </div>

      {/* Fade edges — hints there's more content */}
      <div className="absolute top-0 left-0 bottom-0 w-8 pointer-events-none bg-gradient-to-r from-massclaw-bg to-transparent z-10" />
      <div className="absolute top-0 right-0 bottom-0 w-8 pointer-events-none bg-gradient-to-l from-massclaw-bg to-transparent z-10" />

      {/* Tooltip */}
      <AnimatePresence>
        {hoveredData && !isDragging.current && (
          <motion.div
            initial={{ opacity: 0, y: 6, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.96 }}
            transition={{ duration: 0.12 }}
            className="fixed z-50 w-60 rounded-xl pointer-events-none overflow-hidden"
            style={{
              left: Math.min(Math.max((scrollRef.current?.getBoundingClientRect().left ?? 0) + hoveredData.x - (scrollRef.current?.scrollLeft ?? 0) - 120, 8), window.innerWidth - 260),
              top: Math.max((scrollRef.current?.getBoundingClientRect().top ?? 0) + BASELINE - hoveredData.peakHeight - 95, 4),
            }}
          >
            <div className="relative border border-massclaw-border/60 rounded-xl" style={{ background: "var(--mc-surface)", boxShadow: `0 12px 40px rgba(0,0,0,0.5), 0 0 24px ${hoveredData.status.glow}` }}>
              <div className="absolute inset-x-0 top-0 h-1/3 rounded-t-xl bg-gradient-to-b from-white/[0.05] to-transparent pointer-events-none" />
              <div className="relative p-3">
                <div className="flex items-center gap-2 mb-1.5">
                  <div className="w-2 h-2 rounded-full" style={{ background: hoveredData.status.fill }} />
                  <span className="text-[9px] font-semibold uppercase tracking-widest" style={{ color: hoveredData.status.fill }}>{hoveredData.mission.status}</span>
                  {hoveredData.mission.domain && <span className="text-[8px] text-massclaw-text-muted/40 ml-auto">{hoveredData.mission.domain}</span>}
                </div>
                <p className="text-[11px] text-massclaw-text leading-relaxed line-clamp-2">{truncate(hoveredData.mission.prompt, 90)}</p>
                <div className="flex items-center justify-between mt-2 pt-2 border-t border-massclaw-border/40">
                  <span className="text-[9px] text-massclaw-text-muted/40 font-mono">{hoveredData.mission.budget_used.toFixed(0)}/{hoveredData.mission.budget_limit} cr</span>
                  <span className="text-[8px] text-massclaw-accent/50">click to open</span>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
