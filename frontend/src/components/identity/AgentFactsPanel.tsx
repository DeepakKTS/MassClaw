"use client";

import { useState } from "react";
import {
  BadgeCheck,
  ShieldCheck,
  ShieldAlert,
  ShieldOff,
  CheckCircle2,
  XCircle,
  ExternalLink,
  ChevronDown,
  FileJson,
  Cpu,
  KeyRound,
  Wrench,
  Globe,
  Activity,
  Puzzle,
} from "lucide-react";
import {
  useAgentFacts,
  useInstanceAgentFacts,
  useVerifyAgentFacts,
} from "@/hooks/useAgentFacts";
import { Modal } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { ApiError } from "@/lib/api";
import type {
  AgentFacts,
  AgentFactsSkill,
  VerifyResult,
} from "@/types/agentFacts";

type SignatureState = "signed-unverified" | "verified" | "tampered" | "unsigned";

function StatusPill({ state }: { state: SignatureState }) {
  if (state === "verified") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">
        <ShieldCheck className="w-3 h-3" /> Signed &amp; Verified
      </span>
    );
  }
  if (state === "tampered") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-red-500/15 text-red-400 border border-red-500/20">
        <ShieldAlert className="w-3 h-3" /> Tampered
      </span>
    );
  }
  if (state === "unsigned") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/15 text-amber-400 border border-amber-500/20">
        <ShieldOff className="w-3 h-3" /> Unsigned
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-white/10 text-massclaw-text-muted border border-white/10">
      <ShieldCheck className="w-3 h-3" /> Signed (unverified)
    </span>
  );
}

function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <span className="text-massclaw-accent">{icon}</span>
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-massclaw-text-muted">
        {title}
      </h3>
    </div>
  );
}

function Pill({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-2.5 py-0.5 text-[11px] rounded-full bg-massclaw-accent/10 text-massclaw-accent border border-massclaw-accent/20">
      {children}
    </span>
  );
}

function MutedPill({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-2.5 py-0.5 text-[11px] rounded-full bg-white/5 text-massclaw-text-muted border border-white/10">
      {children}
    </span>
  );
}

function ExternalUrl({ href }: { href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-massclaw-accent hover:underline break-all"
      title={href}
    >
      {href}
      <ExternalLink className="w-3 h-3 shrink-0" />
    </a>
  );
}

function ProviderBlock({ facts }: { facts: AgentFacts }) {
  const { provider } = facts;
  return (
    <div className="glass rounded-lg p-4 space-y-2">
      <SectionHeader icon={<Globe className="w-3.5 h-3.5" />} title="Provider" />
      <p className="text-sm font-medium text-massclaw-text">{provider.name}</p>
      <div className="text-xs">
        <ExternalUrl href={provider.url} />
      </div>
      {provider.did ? (
        <div className="pt-1">
          <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted mb-0.5">
            DID
          </p>
          <p
            className="text-[11px] font-mono break-all text-massclaw-text"
            title={provider.did}
          >
            {provider.did}
          </p>
        </div>
      ) : (
        <p className="text-xs text-massclaw-text-muted italic">
          No provider DID declared
        </p>
      )}
    </div>
  );
}

function EndpointsBlock({ facts }: { facts: AgentFacts }) {
  const { endpoints } = facts;
  return (
    <div className="glass rounded-lg p-4 space-y-3">
      <SectionHeader icon={<Cpu className="w-3.5 h-3.5" />} title="Endpoints" />
      {endpoints.static.length === 0 ? (
        <p className="text-xs text-massclaw-text-muted italic">
          No static endpoints declared
        </p>
      ) : (
        <ul className="space-y-1.5 text-xs list-disc list-inside marker:text-massclaw-text-muted">
          {endpoints.static.map((url) => (
            <li key={url} className="break-all">
              <ExternalUrl href={url} />
            </li>
          ))}
        </ul>
      )}
      {endpoints.adaptive_resolver && (
        <div className="pt-2 border-t border-white/[0.06] space-y-1.5">
          <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted">
            Adaptive resolver
          </p>
          <div className="text-xs">
            <ExternalUrl href={endpoints.adaptive_resolver.url} />
          </div>
          {endpoints.adaptive_resolver.policies &&
            endpoints.adaptive_resolver.policies.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {endpoints.adaptive_resolver.policies.map((p) => (
                  <MutedPill key={p}>{p}</MutedPill>
                ))}
              </div>
            )}
        </div>
      )}
    </div>
  );
}

function CapabilitiesBlock({ facts }: { facts: AgentFacts }) {
  const { capabilities } = facts;
  return (
    <div className="glass rounded-lg p-4 space-y-3">
      <SectionHeader icon={<KeyRound className="w-3.5 h-3.5" />} title="Capabilities" />
      <div>
        <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted mb-1.5">
          Modalities
        </p>
        {capabilities.modalities.length === 0 ? (
          <p className="text-xs text-massclaw-text-muted italic">None declared</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {capabilities.modalities.map((m) => (
              <Pill key={m}>{m}</Pill>
            ))}
          </div>
        )}
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted mb-1.5">
          Auth methods
        </p>
        {capabilities.authentication.methods.length === 0 ? (
          <p className="text-xs text-massclaw-text-muted italic">None declared</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {capabilities.authentication.methods.map((m) => (
              <Pill key={m}>{m}</Pill>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SkillCard({ skill }: { skill: AgentFactsSkill }) {
  return (
    <div className="bg-massclaw-bg rounded-lg p-3 border border-white/[0.04] space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span
          className="text-xs font-mono font-semibold text-massclaw-accent break-all"
          title={skill.id}
        >
          {skill.id}
        </span>
      </div>
      <p className="text-xs text-massclaw-text-muted leading-relaxed">
        {skill.description}
      </p>
      {(skill.inputModes.length > 0 || skill.outputModes.length > 0) && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {skill.inputModes.map((m) => (
            <MutedPill key={`in-${m}`}>in · {m}</MutedPill>
          ))}
          {skill.outputModes.map((m) => (
            <MutedPill key={`out-${m}`}>out · {m}</MutedPill>
          ))}
        </div>
      )}
      {(skill.latencyBudgetMs !== undefined ||
        skill.maxTokens !== undefined ||
        (skill.supportedLanguages && skill.supportedLanguages.length > 0)) && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-[11px] text-massclaw-text-muted">
          {skill.latencyBudgetMs !== undefined && (
            <span>
              latency budget:{" "}
              <span className="text-massclaw-text font-mono">
                {skill.latencyBudgetMs}ms
              </span>
            </span>
          )}
          {skill.maxTokens !== undefined && (
            <span>
              max tokens:{" "}
              <span className="text-massclaw-text font-mono">
                {skill.maxTokens}
              </span>
            </span>
          )}
          {skill.supportedLanguages && skill.supportedLanguages.length > 0 && (
            <span>
              languages:{" "}
              <span className="text-massclaw-text font-mono">
                {skill.supportedLanguages.join(", ")}
              </span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function SkillsBlock({ facts }: { facts: AgentFacts }) {
  return (
    <div className="glass rounded-lg p-4 space-y-3">
      <SectionHeader icon={<Wrench className="w-3.5 h-3.5" />} title="Skills" />
      {facts.skills.length === 0 ? (
        <p className="text-xs text-massclaw-text-muted italic">No skills declared</p>
      ) : (
        <div className="space-y-2">
          {facts.skills.map((skill) => (
            <SkillCard key={skill.id} skill={skill} />
          ))}
        </div>
      )}
    </div>
  );
}

function CertificationBlock({ facts }: { facts: AgentFacts }) {
  if (!facts.certification) return null;
  const { level, issuer, issuedAt, validUntil } = facts.certification;
  return (
    <div className="glass rounded-lg p-4 space-y-2">
      <SectionHeader icon={<BadgeCheck className="w-3.5 h-3.5" />} title="Certification" />
      <div className="flex items-center gap-2 flex-wrap">
        <span className="px-2.5 py-0.5 text-[11px] rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
          {level}
        </span>
        {issuer && (
          <span className="text-xs text-massclaw-text-muted">
            issued by <span className="text-massclaw-text">{issuer}</span>
          </span>
        )}
      </div>
      {(issuedAt || validUntil) && (
        <div className="text-[11px] text-massclaw-text-muted grid grid-cols-2 gap-2 pt-1">
          {issuedAt && (
            <div>
              <p className="text-[10px] uppercase tracking-wider">Issued</p>
              <p className="text-massclaw-text font-mono">{issuedAt}</p>
            </div>
          )}
          {validUntil && (
            <div>
              <p className="text-[10px] uppercase tracking-wider">Valid until</p>
              <p className="text-massclaw-text font-mono">{validUntil}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TelemetryBlock({ facts }: { facts: AgentFacts }) {
  const metrics = facts.telemetry?.metrics;
  if (!metrics) return null;
  const hasAny =
    metrics.throughput_rps !== undefined ||
    metrics.availability !== undefined ||
    metrics.latency_p95_ms !== undefined;
  if (!hasAny) return null;
  return (
    <div className="glass rounded-lg p-4 space-y-2">
      <SectionHeader icon={<Activity className="w-3.5 h-3.5" />} title="Telemetry" />
      <div className="grid grid-cols-3 gap-3 text-xs">
        {metrics.throughput_rps !== undefined && (
          <div>
            <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted">
              Throughput
            </p>
            <p className="text-sm font-semibold text-massclaw-text">
              {metrics.throughput_rps} rps
            </p>
          </div>
        )}
        {metrics.availability !== undefined && (
          <div>
            <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted">
              Availability
            </p>
            <p className="text-sm font-semibold text-massclaw-text">
              {(metrics.availability * 100).toFixed(2)}%
            </p>
          </div>
        )}
        {metrics.latency_p95_ms !== undefined && (
          <div>
            <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted">
              P95 Latency
            </p>
            <p className="text-sm font-semibold text-massclaw-text">
              {metrics.latency_p95_ms}ms
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function ExtensionsBlock({ facts }: { facts: AgentFacts }) {
  const ext = facts["x-massclaw"];
  if (!ext) return null;
  const { limits, nanda_index_handle, example_requests, error_schema } = ext;
  return (
    <div className="glass rounded-lg p-4 space-y-3">
      <SectionHeader
        icon={<Puzzle className="w-3.5 h-3.5" />}
        title="MassClaw Extensions"
      />
      {nanda_index_handle && (
        <div className="text-xs">
          <span className="text-massclaw-text-muted">NANDA handle: </span>
          <span className="font-mono text-massclaw-text break-all">
            {nanda_index_handle}
          </span>
        </div>
      )}
      {limits && Object.keys(limits).length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted">
            Limits
          </p>
          <div className="space-y-0.5 text-xs font-mono">
            {Object.entries(limits).map(([k, v]) => (
              <div
                key={k}
                className="flex items-center justify-between gap-3 bg-massclaw-bg/60 rounded px-2 py-1 border border-white/[0.04]"
              >
                <span className="text-massclaw-text-muted">{k}</span>
                <span className="text-massclaw-text break-all text-right">
                  {typeof v === "object" ? JSON.stringify(v) : String(v)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      {example_requests && example_requests.length > 0 && (
        <details className="group">
          <summary className="flex items-center gap-1 text-xs text-massclaw-text-muted cursor-pointer hover:text-massclaw-accent transition-colors">
            <ChevronDown className="w-3 h-3 transition-transform group-open:rotate-0 -rotate-90" />
            Example requests ({example_requests.length})
          </summary>
          <pre className="mt-2 text-[11px] font-mono bg-massclaw-bg/60 rounded-lg p-3 overflow-x-auto text-massclaw-text-muted border border-white/[0.04]">
            {JSON.stringify(example_requests, null, 2)}
          </pre>
        </details>
      )}
      {error_schema && Object.keys(error_schema).length > 0 && (
        <details className="group">
          <summary className="flex items-center gap-1 text-xs text-massclaw-text-muted cursor-pointer hover:text-massclaw-accent transition-colors">
            <ChevronDown className="w-3 h-3 transition-transform group-open:rotate-0 -rotate-90" />
            Error schema
          </summary>
          <pre className="mt-2 text-[11px] font-mono bg-massclaw-bg/60 rounded-lg p-3 overflow-x-auto text-massclaw-text-muted border border-white/[0.04]">
            {JSON.stringify(error_schema, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="glass rounded-xl p-5 border border-massclaw-border space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 space-y-2">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-3 w-64" />
        </div>
        <Skeleton className="h-7 w-24" />
      </div>
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

function VerifyModal({
  open,
  onClose,
  result,
  isPending,
}: {
  open: boolean;
  onClose: () => void;
  result: VerifyResult | undefined;
  isPending: boolean;
}) {
  return (
    <Modal open={open} onClose={onClose} title="AgentFacts Signature Verification">
      {isPending && (
        <p className="text-sm text-massclaw-text-muted">Verifying signature…</p>
      )}
      {!isPending && result && (
        <div className="space-y-4">
          <div
            className={`flex items-center gap-3 p-3 rounded-lg border ${
              result.valid
                ? "bg-emerald-500/10 border-emerald-500/20"
                : "bg-red-500/10 border-red-500/20"
            }`}
          >
            {result.valid ? (
              <CheckCircle2 className="w-6 h-6 text-emerald-400 shrink-0" />
            ) : (
              <XCircle className="w-6 h-6 text-red-400 shrink-0" />
            )}
            <div>
              <p
                className={`font-semibold text-sm ${
                  result.valid ? "text-emerald-400" : "text-red-400"
                }`}
              >
                {result.valid ? "Signature valid" : "Signature invalid"}
              </p>
              <p className="text-[11px] text-massclaw-text-muted">
                {result.valid
                  ? "The document's Ed25519 integrity credential verifies against its provider DID."
                  : "One or more integrity credentials failed to verify."}
              </p>
            </div>
          </div>

          {(result.label || result.agent_id || result.provider_did) && (
            <div className="space-y-2 text-xs">
              {result.label && (
                <div>
                  <span className="text-massclaw-text-muted">Label: </span>
                  <span className="text-massclaw-text">{result.label}</span>
                </div>
              )}
              {result.agent_id && (
                <div>
                  <span className="text-massclaw-text-muted">Agent ID: </span>
                  <span
                    className="font-mono text-massclaw-text break-all"
                    title={result.agent_id}
                  >
                    {result.agent_id}
                  </span>
                </div>
              )}
              {result.provider_did && (
                <div>
                  <span className="text-massclaw-text-muted">Provider DID: </span>
                  <span
                    className="font-mono text-massclaw-text break-all"
                    title={result.provider_did}
                  >
                    {result.provider_did}
                  </span>
                </div>
              )}
            </div>
          )}

          {!result.valid && result.errors.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wider text-massclaw-text-muted">
                Errors
              </p>
              <ul className="text-xs text-red-400 space-y-1 list-disc list-inside">
                {result.errors.map((e, i) => (
                  <li key={i} className="break-words">
                    {e}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function PanelBody({
  facts,
  verifyMutation,
}: {
  facts: AgentFacts;
  verifyMutation: ReturnType<typeof useVerifyAgentFacts>;
}) {
  const [modalOpen, setModalOpen] = useState(false);

  const hasCredentials = facts.verifiable_credentials.length > 0;
  const verifyAttempted = verifyMutation.isSuccess;
  const verifyOk = verifyMutation.data?.valid === true;

  let signatureState: SignatureState;
  if (!hasCredentials) {
    signatureState = "unsigned";
  } else if (verifyAttempted && !verifyOk) {
    signatureState = "tampered";
  } else if (verifyAttempted && verifyOk) {
    signatureState = "verified";
  } else {
    signatureState = "signed-unverified";
  }

  const handleVerify = () => {
    verifyMutation.mutate(facts);
    setModalOpen(true);
  };

  return (
    <div className="glass rounded-xl p-5 border border-massclaw-border space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-sm font-semibold text-massclaw-text">
              {facts.label}
            </h2>
            <span className="text-[10px] text-massclaw-text-muted font-mono">
              v{facts.version}
            </span>
            <StatusPill state={signatureState} />
          </div>
          <p
            className="text-[11px] font-mono text-massclaw-text-muted break-all"
            title={facts.id}
          >
            {facts.id}
          </p>
          {facts.provider.did && (
            <p
              className="text-[11px] font-mono text-massclaw-text-muted truncate"
              title={facts.provider.did}
            >
              {facts.provider.did}
            </p>
          )}
        </div>
        <button
          onClick={handleVerify}
          disabled={!hasCredentials || verifyMutation.isPending}
          title={
            !hasCredentials
              ? "Document has no verifiable_credentials to verify"
              : "Re-verify the Ed25519 integrity credential"
          }
          className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-massclaw-accent/15 text-massclaw-accent border border-massclaw-accent/30 hover:bg-massclaw-accent/25 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          <ShieldCheck className="w-3.5 h-3.5" />
          {verifyMutation.isPending ? "Verifying…" : "Verify Signature"}
        </button>
      </div>

      {/* Description */}
      {facts.description && (
        <p className="text-xs text-massclaw-text-muted leading-relaxed">
          {facts.description}
        </p>
      )}

      {/* Provider */}
      <ProviderBlock facts={facts} />

      {/* Endpoints */}
      <EndpointsBlock facts={facts} />

      {/* Capabilities */}
      <CapabilitiesBlock facts={facts} />

      {/* Skills */}
      <SkillsBlock facts={facts} />

      {/* Certification */}
      <CertificationBlock facts={facts} />

      {/* Telemetry */}
      <TelemetryBlock facts={facts} />

      {/* MassClaw extensions */}
      <ExtensionsBlock facts={facts} />

      {/* Raw JSON viewer */}
      <details className="group">
        <summary className="flex items-center gap-1.5 text-xs text-massclaw-text-muted cursor-pointer hover:text-massclaw-accent transition-colors">
          <ChevronDown className="w-3 h-3 transition-transform group-open:rotate-0 -rotate-90" />
          <FileJson className="w-3.5 h-3.5" />
          Raw AgentFacts JSON
        </summary>
        <pre className="mt-2 text-[11px] font-mono bg-massclaw-bg rounded-lg p-4 overflow-auto text-massclaw-text-muted border border-white/[0.04] max-h-96">
          {JSON.stringify(facts, null, 2)}
        </pre>
      </details>

      <VerifyModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        result={verifyMutation.data}
        isPending={verifyMutation.isPending}
      />
    </div>
  );
}

export function AgentFactsPanel({
  agentId,
  instance,
}: {
  agentId?: string;
  instance?: boolean;
}) {
  const invalidProps = (!agentId && !instance) || (agentId && instance);

  // Hooks must run unconditionally before any early returns. One of the two
  // queries is always disabled via its own `enabled` guard below.
  const perAgent = useAgentFacts(!instance && agentId ? agentId : "");
  const perInstance = useInstanceAgentFacts({ enabled: !!instance });
  const verifyMutation = useVerifyAgentFacts();

  // Exactly-one invariant at runtime — protects the one-of-two API contract.
  if (invalidProps) {
    return (
      <div className="glass rounded-xl p-5 border border-massclaw-danger/20 text-xs text-massclaw-danger">
        AgentFactsPanel requires exactly one of <code>agentId</code> or{" "}
        <code>instance</code>.
      </div>
    );
  }

  const active = instance ? perInstance : perAgent;

  if (active.isPending) {
    return <LoadingSkeleton />;
  }

  if (active.isError) {
    const err = active.error;
    const is404 = err instanceof ApiError && err.status === 404;
    if (is404) {
      return (
        <div className="glass rounded-xl p-5 border border-massclaw-border text-xs text-massclaw-text-muted">
          No AgentFacts document available for this agent yet.
        </div>
      );
    }
    const message =
      err instanceof Error ? err.message : "Unknown error fetching AgentFacts";
    return (
      <div className="glass rounded-xl p-5 border border-massclaw-danger/20 space-y-1">
        <p className="text-xs font-semibold text-massclaw-danger">
          AgentFacts unavailable
        </p>
        <p className="text-xs text-massclaw-text-muted break-words">{message}</p>
      </div>
    );
  }

  if (!active.data) {
    return (
      <div className="glass rounded-xl p-5 border border-massclaw-border text-xs text-massclaw-text-muted">
        No AgentFacts document available for this agent yet.
      </div>
    );
  }

  return <PanelBody facts={active.data} verifyMutation={verifyMutation} />;
}
