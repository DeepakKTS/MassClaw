"use client";

import { useState } from "react";
import { Play, Loader2, Terminal } from "lucide-react";
import { api } from "@/lib/api";

const LANGUAGES = [
  { value: "python", label: "Python" },
  { value: "javascript", label: "JavaScript" },
  { value: "shell", label: "Shell" },
];

const EXAMPLES: Record<string, string> = {
  python: 'import sys\nprint(f"Python {sys.version}")\nprint("Hello from MassClaw sandbox!")\n\n# Math\nresult = sum(range(1, 101))\nprint(f"Sum 1-100 = {result}")',
  javascript: 'console.log(`Node.js ${process.version}`);\nconsole.log("Hello from MassClaw sandbox!");\n\n// Math\nconst result = Array.from({length: 100}, (_, i) => i + 1).reduce((a, b) => a + b);\nconsole.log(`Sum 1-100 = ${result}`);',
  shell: 'echo "Hello from MassClaw sandbox!"\necho "User: $(whoami)"\necho "Working dir: $(pwd)"\necho "Date: $(date)"',
};

export default function PlaygroundPage() {
  const [language, setLanguage] = useState("python");
  const [code, setCode] = useState(EXAMPLES.python);
  const [output, setOutput] = useState("");
  const [running, setRunning] = useState(false);
  const [metadata, setMetadata] = useState<Record<string, unknown> | null>(null);

  const runCode = async () => {
    setRunning(true);
    setOutput("");
    setMetadata(null);
    try {
      const result = await api.post<{ success: boolean; output: string; metadata: Record<string, unknown> }>(
        "/tools/code_execute/run",
        { language, code, timeout: 30 }
      );
      setOutput(result.output);
      setMetadata(result.metadata);
    } catch (err: unknown) {
      setOutput(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-4 py-6">
      <div>
        <h1 className="text-heading flex items-center gap-2">
          <Terminal size={24} className="text-massclaw-accent" />
          Code Playground
        </h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">
          Execute code in a sandboxed Docker container. Network disabled, CPU/memory limited.
        </p>
      </div>

      <div className="flex gap-2 items-center">
        {LANGUAGES.map((lang) => (
          <button
            key={lang.value}
            onClick={() => { setLanguage(lang.value); setCode(EXAMPLES[lang.value]); }}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              language === lang.value
                ? "bg-massclaw-accent text-white"
                : "bg-white/[0.06] text-massclaw-text-muted hover:text-white"
            }`}
          >
            {lang.label}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={runCode}
          disabled={running || !code.trim()}
          className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-massclaw-success/20 text-massclaw-success text-xs font-medium hover:bg-massclaw-success/30 disabled:opacity-50 transition-colors"
        >
          {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {running ? "Running..." : "Run"}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-1">
          <label className="text-[10px] font-semibold text-massclaw-text-muted uppercase tracking-wider">Code</label>
          <textarea
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="w-full h-80 bg-massclaw-bg/80 border border-white/[0.08] rounded-xl p-4 font-mono text-sm text-massclaw-text resize-none focus:outline-none focus:border-massclaw-accent/40"
            spellCheck={false}
          />
        </div>

        <div className="space-y-1">
          <label className="text-[10px] font-semibold text-massclaw-text-muted uppercase tracking-wider">Output</label>
          <div className="w-full h-80 bg-massclaw-bg/80 border border-white/[0.08] rounded-xl p-4 font-mono text-sm overflow-auto">
            {output ? (
              <pre className="whitespace-pre-wrap text-massclaw-text-muted">{output}</pre>
            ) : (
              <p className="text-massclaw-text-muted/40 italic">Run code to see output...</p>
            )}
          </div>
          {metadata && (
            <div className="flex gap-3 text-[10px] text-massclaw-text-muted">
              {metadata.exit_code !== undefined && (
                <span>Exit: <span className={metadata.exit_code === 0 ? "text-massclaw-success" : "text-massclaw-danger"}>{String(metadata.exit_code)}</span></span>
              )}
              {metadata.execution_time_ms !== undefined && (
                <span>Time: {String(metadata.execution_time_ms)}ms</span>
              )}
              {metadata.language !== undefined && <span>Lang: {String(metadata.language)}</span>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
