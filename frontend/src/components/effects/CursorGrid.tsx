"use client";

import { useEffect, useRef } from "react";

const DOT_SPACING = 28;
const DOT_RADIUS = 1;
const GLOW_RADIUS = 120;

export function CursorGrid() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mouseRef = useRef({ x: -1000, y: -1000 });
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = window.innerWidth * dpr;
      canvas.height = window.innerHeight * dpr;
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;
      ctx.scale(dpr, dpr);
    };
    resize();
    window.addEventListener("resize", resize);

    const onMove = (e: MouseEvent) => {
      mouseRef.current = { x: e.clientX, y: e.clientY };
    };
    const onLeave = () => {
      mouseRef.current = { x: -1000, y: -1000 };
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseleave", onLeave);

    const isDark = () => document.documentElement.classList.contains("light") === false;

    const draw = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      const mx = mouseRef.current.x;
      const my = mouseRef.current.y;
      const dark = isDark();

      ctx.clearRect(0, 0, w, h);

      const cols = Math.ceil(w / DOT_SPACING) + 1;
      const rows = Math.ceil(h / DOT_SPACING) + 1;
      const offsetX = (w % DOT_SPACING) / 2;
      const offsetY = (h % DOT_SPACING) / 2;

      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const x = offsetX + c * DOT_SPACING;
          const y = offsetY + r * DOT_SPACING;

          const dx = x - mx;
          const dy = y - my;
          const dist = Math.sqrt(dx * dx + dy * dy);

          let alpha: number;
          let radius: number;

          if (dist < GLOW_RADIUS) {
            const t = 1 - dist / GLOW_RADIUS;
            const ease = t * t * (3 - 2 * t); // smoothstep
            alpha = dark ? 0.04 + ease * 0.35 : 0.03 + ease * 0.2;
            radius = DOT_RADIUS + ease * 1.2;
          } else {
            alpha = dark ? 0.04 : 0.03;
            radius = DOT_RADIUS;
          }

          ctx.beginPath();
          ctx.arc(x, y, radius, 0, Math.PI * 2);
          ctx.fillStyle = dark
            ? `rgba(196, 120, 50, ${alpha})`
            : `rgba(166, 100, 40, ${alpha})`;
          ctx.fill();
        }
      }

      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseleave", onLeave);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none z-0"
      aria-hidden="true"
    />
  );
}
