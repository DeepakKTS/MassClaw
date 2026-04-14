"use client";

import { cn } from "@/lib/utils";

interface TrustRingProps {
  score: number; // 0-1
  size?: number;
  className?: string;
}

export function TrustRing({ score, size = 36, className }: TrustRingProps) {
  const radius = (size - 4) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = circumference * Math.max(0, Math.min(1, score));
  const color =
    score >= 0.8
      ? "#22c55e"
      : score >= 0.5
      ? "#f59e0b"
      : "#ef4444";

  return (
    <svg
      width={size}
      height={size}
      className={cn("shrink-0", className)}
      viewBox={`0 0 ${size} ${size}`}
    >
      {/* Background ring */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="rgba(255,255,255,0.06)"
        strokeWidth={2}
      />
      {/* Filled ring */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        className="transition-all duration-700"
        style={{ filter: `drop-shadow(0 0 4px ${color}40)` }}
      />
      {/* Score text */}
      <text
        x={size / 2}
        y={size / 2}
        textAnchor="middle"
        dominantBaseline="central"
        className="fill-massclaw-text"
        fontSize={size * 0.28}
        fontWeight={600}
      >
        {(score * 100).toFixed(0)}
      </text>
    </svg>
  );
}
