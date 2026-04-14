"use client";

import { cn } from "@/lib/utils";
import { Loader2 } from "lucide-react";
import { motion } from "framer-motion";
import { type ReactNode } from "react";

const variants = {
  primary: "bg-massclaw-accent text-white hover:bg-massclaw-accent-light",
  secondary: "bg-massclaw-surface text-massclaw-text border border-massclaw-border hover:bg-massclaw-border/50",
  danger: "bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20",
  ghost: "text-massclaw-text-muted hover:text-massclaw-text hover:bg-massclaw-border/30",
  glow: "bg-massclaw-accent text-white shadow-lg shadow-massclaw-accent/20 hover:shadow-massclaw-accent/30 hover:bg-massclaw-accent-light glow-pulse",
};

const sizes = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-4 py-2 text-sm",
  lg: "px-6 py-3 text-base",
};

interface ButtonProps {
  variant?: keyof typeof variants;
  size?: keyof typeof sizes;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  children?: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit" | "reset";
}

export function Button({
  variant = "primary",
  size = "md",
  loading,
  className,
  children,
  disabled,
  onClick,
  type = "button",
}: ButtonProps) {
  return (
    <motion.button
      type={type}
      disabled={disabled || loading}
      onClick={onClick}
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.97 }}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
        variants[variant],
        sizes[size],
        className
      )}
    >
      {loading && <Loader2 size={14} className="animate-spin" />}
      {children}
    </motion.button>
  );
}
