"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { Rocket, Loader2, Info, Zap, BarChart3, Brain, Sparkles, Mic, Square } from "lucide-react";
import { useCreateWorkflow } from "@/hooks/useWorkflows";
import { api } from "@/lib/api";

interface BudgetEstimate {
  estimated_budget: number;
  estimated_ops: number;
  complexity: string;
  confidence: number;
}

const PRESETS = [
  { label: "Quick", value: 100, icon: Zap, color: "#22d3ee" },
  { label: "Standard", value: 500, icon: BarChart3, color: "#FF7A1A" },
  { label: "Deep", value: 1500, icon: Brain, color: "#4A8EC2" },
] as const;

const VISUALIZER_BARS = 28;

export function MissionInput() {
  const router = useRouter();
  const createWorkflow = useCreateWorkflow();
  const [prompt, setPrompt] = useState("");
  const [budget, setBudget] = useState(500);
  const [showBudget, setShowBudget] = useState(false);
  const [showTooltip, setShowTooltip] = useState(false);
  const [estimate, setEstimate] = useState<BudgetEstimate | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  // Voice recording state
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [speechSupported, setSpeechSupported] = useState(true);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const recognitionRef = useRef<any>(null);
  const timerRef = useRef<ReturnType<typeof setInterval>>();
  const [barHeights, setBarHeights] = useState<number[]>(() =>
    Array.from({ length: VISUALIZER_BARS }, () => 15 + Math.random() * 30)
  );

  // Check SpeechRecognition support
  useEffect(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setSpeechSupported(false);
      return;
    }
    const recognition = new SR();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: any) => {
      let transcript = "";
      for (let i = 0; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      setPrompt(transcript);
    };

    recognition.onerror = (event: any) => {
      if (event.error === "not-allowed") {
        setVoiceError("Microphone access denied");
      } else if (event.error === "no-speech") {
        setVoiceError("No speech detected");
      } else {
        setVoiceError(`Error: ${event.error}`);
      }
      stopRecording();
    };

    recognition.onend = () => {
      // If still in recording state, it ended unexpectedly
      if (isRecording) stopRecording();
    };

    recognitionRef.current = recognition;
  }, []);

  // Animate visualizer bars while recording
  useEffect(() => {
    if (!isRecording) return;
    const interval = setInterval(() => {
      setBarHeights(Array.from({ length: VISUALIZER_BARS }, () => 12 + Math.random() * 88));
    }, 120);
    return () => clearInterval(interval);
  }, [isRecording]);

  // Recording timer
  useEffect(() => {
    if (isRecording) {
      setRecordingTime(0);
      timerRef.current = setInterval(() => setRecordingTime((t) => t + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [isRecording]);

  const startRecording = () => {
    if (!speechSupported) {
      setVoiceError("Voice not supported in this browser");
      setTimeout(() => setVoiceError(null), 3000);
      return;
    }
    setVoiceError(null);
    setShowBudget(false);
    setIsRecording(true);
    try {
      recognitionRef.current?.start();
    } catch {
      // Already started
    }
  };

  const stopRecording = () => {
    setIsRecording(false);
    try {
      recognitionRef.current?.stop();
    } catch {
      // Already stopped
    }
    // After stopping, the transcript is in `prompt` via onresult
    // Budget analyzer will fire via the debounced effect
  };

  // Auto-resize textarea
  useEffect(() => {
    if (!textareaRef.current || isRecording) return;
    textareaRef.current.style.height = "auto";
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
  }, [prompt, isRecording]);

  // Prompt complexity analyzer
  const analyzePrompt = useCallback(async (text: string) => {
    if (text.trim().length < 5) { setEstimate(null); return; }
    try {
      const result = await api.post<BudgetEstimate>("/workflows/estimate-budget", { prompt: text });
      setEstimate(result);
    } catch {
      const words = text.split(/\s+/).length;
      const ops = Math.max(2, Math.min(12, Math.floor(words / 8) + 2));
      setEstimate({
        estimated_budget: Math.max(50, Math.round((ops * 20) / 50) * 50),
        estimated_ops: ops, complexity: words < 15 ? "simple" : words < 40 ? "moderate" : "complex", confidence: 0.5,
      });
    }
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => analyzePrompt(prompt), 600);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [prompt, analyzePrompt]);

  const handleSubmit = async () => {
    if (!prompt.trim() || createWorkflow.isPending) return;
    try {
      const wf = await createWorkflow.mutateAsync({ prompt: prompt.trim(), budget_limit: budget });
      setPrompt("");
      router.push(`/missions/${wf.workflow_id}`);
    } catch (err) {
      console.error("Failed to launch mission:", err);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const hasContent = prompt.trim().length > 0;
  const budgetPercent = ((budget - 50) / (2000 - 50)) * 100;
  const formatTime = (s: number) => `${Math.floor(s / 60).toString().padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring" as const, stiffness: 300, damping: 30, delay: 0.1 }}
      className="w-full max-w-2xl mx-auto"
    >
      <div
        className={`rounded-3xl border p-3 shadow-[0_8px_40px_rgba(0,0,0,0.35)] transition-all duration-300 ${
          isRecording ? "border-red-500/30" : "border-[var(--mc-border)]"
        }`}
        style={{ background: "var(--mc-surface)", backdropFilter: "blur(20px)" }}
      >
        {/* Smart estimate */}
        <AnimatePresence>
          {estimate && hasContent && !isRecording && (
            <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-1.5 mb-1.5 rounded-xl bg-massclaw-accent/[0.06]">
                <Sparkles size={10} className="text-massclaw-accent shrink-0" />
                <span className="text-[10px] text-massclaw-accent">
                  ~{estimate.estimated_budget} cr &middot; {estimate.estimated_ops} ops &middot; {estimate.complexity}
                </span>
                {estimate.estimated_budget !== budget && (
                  <button type="button" onClick={() => setBudget(estimate.estimated_budget)}
                    className="ml-auto text-[9px] text-massclaw-accent/70 hover:text-massclaw-accent underline underline-offset-2">Apply</button>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Voice error */}
        <AnimatePresence>
          {voiceError && (
            <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-1.5 mb-1.5 rounded-xl bg-red-500/[0.08]">
                <span className="text-[10px] text-red-400">{voiceError}</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ─── Recording Visualizer ─── */}
        <AnimatePresence mode="wait">
          {isRecording ? (
            <motion.div
              key="recorder"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.25 }}
              className="px-3 py-4"
            >
              {/* Timer */}
              <div className="flex items-center justify-center gap-2 mb-4">
                <div className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
                <span className="font-mono text-sm text-white/80 tracking-wider">{formatTime(recordingTime)}</span>
              </div>

              {/* Waveform */}
              <div className="w-full h-12 flex items-center justify-center gap-[2px] px-2">
                {barHeights.map((h, i) => (
                  <motion.div
                    key={i}
                    className="w-[3px] rounded-full bg-gradient-to-t from-red-500/40 to-red-400/80"
                    animate={{ height: `${h}%` }}
                    transition={{ duration: 0.12, ease: "easeOut" }}
                  />
                ))}
              </div>

              {/* Transcript preview */}
              {prompt && (
                <motion.p
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="text-[11px] text-massclaw-text-muted/60 text-center mt-3 line-clamp-2 italic"
                >
                  &ldquo;{prompt}&rdquo;
                </motion.p>
              )}
            </motion.div>
          ) : (
            <motion.div key="textarea" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }}>
              <textarea
                ref={textareaRef}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="What do you want your agents to accomplish?"
                rows={1}
                disabled={createWorkflow.isPending}
                className="w-full bg-transparent border-none text-[15px] text-massclaw-text placeholder:text-massclaw-text-muted/40 leading-relaxed resize-none focus:outline-none focus:ring-0 px-3 py-2 min-h-[44px] scrollbar-thin tracking-tight"
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Budget panel — hidden during recording */}
        <AnimatePresence>
          {showBudget && !isRecording && (
            <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.2 }} className="overflow-hidden">
              <div className="px-2 pt-1 pb-2 space-y-2.5">
                <div className="flex items-center gap-1.5">
                  {PRESETS.map((preset) => {
                    const Icon = preset.icon;
                    const isActive = budget === preset.value;
                    return (
                      <button key={preset.label} type="button" onClick={() => setBudget(preset.value)}
                        className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-medium transition-all border ${isActive ? "border-massclaw-accent/30 text-massclaw-accent" : "border-transparent text-massclaw-text-muted/50 hover:text-massclaw-text-muted"}`}
                        style={isActive ? { background: `${preset.color}12` } : {}}>
                        <Icon size={10} />{preset.label}
                      </button>
                    );
                  })}
                </div>
                <div className="relative h-6 flex items-center px-1">
                  <div className="absolute inset-x-1 h-[6px] rounded-full" style={{ background: "rgba(255,255,255,0.04)" }}>
                    <motion.div className="h-full rounded-full relative overflow-hidden" animate={{ width: `${budgetPercent}%` }} transition={{ type: "spring" as const, stiffness: 300, damping: 30 }}>
                      <div className="absolute inset-0 bg-massclaw-accent/50" />
                      <div className="absolute inset-0 bg-gradient-to-b from-white/10 to-transparent" style={{ height: "50%" }} />
                    </motion.div>
                  </div>
                  <input type="range" min={50} max={2000} step={50} value={budget} onChange={(e) => setBudget(Number(e.target.value))} className="absolute inset-x-1 w-[calc(100%-8px)] h-6 opacity-0 cursor-pointer z-10" />
                  <motion.div className="absolute top-1/2 -translate-y-1/2 pointer-events-none" animate={{ left: `calc(${budgetPercent}% + 4px - 6px)` }} transition={{ type: "spring" as const, stiffness: 300, damping: 30 }}>
                    <div className="w-3 h-3 rounded-full border-2 border-massclaw-accent bg-[var(--mc-bg)] shadow-lg shadow-massclaw-accent/30" />
                  </motion.div>
                </div>
                <div className="flex items-center justify-between px-1">
                  <span className="text-[8px] text-massclaw-text-muted/30">50</span>
                  <motion.span key={budget} initial={{ opacity: 0, y: -3 }} animate={{ opacity: 1, y: 0 }} className="text-xs font-mono font-semibold text-massclaw-accent">{budget} cr</motion.span>
                  <span className="text-[8px] text-massclaw-text-muted/30">2000</span>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ─── Action Bar ─── */}
        <div className="flex items-center justify-between pt-1.5 px-1">
          {/* Left actions — hidden during recording */}
          <div className={`flex items-center gap-0.5 transition-all duration-200 ${isRecording ? "opacity-0 pointer-events-none w-0" : "opacity-100"}`}>
            <button type="button" onClick={() => setShowBudget(!showBudget)}
              className={`flex items-center gap-1 px-2.5 py-1.5 rounded-full text-[11px] transition-all border h-8 ${showBudget ? "bg-massclaw-accent/10 border-massclaw-accent/20 text-massclaw-accent" : "bg-transparent border-transparent text-massclaw-text-muted/50 hover:text-massclaw-text-muted"}`}>
              <motion.div animate={{ rotate: showBudget ? 360 : 0, scale: showBudget ? 1.1 : 1 }} transition={{ type: "spring", stiffness: 260, damping: 25 }}><Zap size={14} /></motion.div>
              <AnimatePresence>
                {showBudget && (
                  <motion.span initial={{ width: 0, opacity: 0 }} animate={{ width: "auto", opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={{ duration: 0.15 }} className="overflow-hidden whitespace-nowrap text-[10px]">{budget} cr</motion.span>
                )}
              </AnimatePresence>
            </button>
            <div className="h-5 w-px mx-1" style={{ background: "linear-gradient(to bottom, transparent, rgba(255, 122, 26,0.3), transparent)" }} />
            <div className="relative">
              <button type="button" onMouseEnter={() => setShowTooltip(true)} onMouseLeave={() => setShowTooltip(false)}
                className="flex items-center justify-center w-8 h-8 rounded-full text-massclaw-text-muted/30 hover:text-massclaw-text-muted/60 transition-colors"><Info size={14} /></button>
              <AnimatePresence>
                {showTooltip && (
                  <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 4 }}
                    className="absolute left-0 bottom-10 z-50 w-48 p-2.5 rounded-xl text-[10px] leading-relaxed border border-[var(--mc-border)] shadow-xl"
                    style={{ background: "var(--mc-surface)", backdropFilter: "blur(20px)" }}>
                    <p className="text-massclaw-text mb-1 font-medium">How credits work</p>
                    <p className="text-massclaw-text-muted/60">Each agent op costs ~10-50 credits. A typical mission uses 5-8 ops.</p>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>

          {/* Right: Launch / Mic / Stop */}
          <motion.button type="button"
            onClick={() => {
              if (isRecording) { stopRecording(); }
              else if (hasContent) { handleSubmit(); }
              else { startRecording(); }
            }}
            disabled={createWorkflow.isPending && !hasContent}
            whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}
            className={`h-8 w-8 rounded-full flex items-center justify-center transition-all duration-200 ${
              isRecording
                ? "bg-red-500/15 text-red-400 hover:bg-red-500/25"
                : createWorkflow.isPending
                  ? "bg-red-500/20 text-red-400"
                  : hasContent
                    ? "bg-white text-[var(--mc-bg)] shadow-lg shadow-white/10"
                    : "bg-transparent text-massclaw-text-muted/40 hover:text-massclaw-text-muted/70 hover:bg-white/[0.04]"
            } disabled:opacity-30 disabled:cursor-not-allowed`}
          >
            {createWorkflow.isPending ? (
              <Loader2 size={16} className="animate-spin" />
            ) : isRecording ? (
              <Square size={14} className="fill-red-400" />
            ) : hasContent ? (
              <Rocket size={15} />
            ) : (
              <Mic size={16} />
            )}
          </motion.button>
        </div>

        {createWorkflow.isError && (
          <p className="text-red-400/80 text-[10px] px-3 pt-1">{(createWorkflow.error as Error).message}</p>
        )}
      </div>
    </motion.div>
  );
}
