"use client";

import {
  createContext,
  useContext,
  useCallback,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/utils";
import { X, AlertCircle, CheckCircle, Info } from "lucide-react";

type ToastVariant = "error" | "success" | "info";

interface Toast {
  id: string;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  addToast: (message: string, variant?: ToastVariant) => void;
  removeToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let globalAddToast: ((message: string, variant?: ToastVariant) => void) | null = null;

/** Fire a toast from outside React (e.g. the api client). */
export function showToast(message: string, variant: ToastVariant = "error") {
  if (globalAddToast) {
    globalAddToast(message, variant);
  }
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

const VARIANT_STYLES: Record<ToastVariant, string> = {
  error: "border-massclaw-danger/40 bg-massclaw-danger/10",
  success: "border-massclaw-success/40 bg-massclaw-success/10",
  info: "border-massclaw-accent/40 bg-massclaw-accent/10",
};

const VARIANT_ICON: Record<ToastVariant, typeof AlertCircle> = {
  error: AlertCircle,
  success: CheckCircle,
  info: Info,
};

const VARIANT_TEXT: Record<ToastVariant, string> = {
  error: "text-massclaw-danger",
  success: "text-massclaw-success",
  info: "text-massclaw-accent",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((message: string, variant: ToastVariant = "error") => {
    const id = crypto.randomUUID();
    setToasts((prev) => [...prev, { id, message, variant }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Expose globally for non-React callers (api client)
  globalAddToast = addToast;

  return (
    <ToastContext.Provider value={{ addToast, removeToast }}>
      {children}
      <div className="fixed bottom-28 right-6 z-[100] flex flex-col gap-2 pointer-events-none">
        {toasts.map((toast) => {
          const Icon = VARIANT_ICON[toast.variant];
          return (
            <div
              key={toast.id}
              className={cn(
                "pointer-events-auto flex items-start gap-3 rounded-xl border px-4 py-3 shadow-lg backdrop-blur-md animate-in slide-in-from-right-5 fade-in duration-300 max-w-sm",
                VARIANT_STYLES[toast.variant]
              )}
            >
              <Icon size={16} className={cn("mt-0.5 shrink-0", VARIANT_TEXT[toast.variant])} />
              <p className="text-sm text-massclaw-text flex-1">{toast.message}</p>
              <button
                onClick={() => removeToast(toast.id)}
                className="shrink-0 text-massclaw-text-muted hover:text-massclaw-text transition-colors"
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
