/**
 * AgentFacts v1 — NANDA-conformant identity document.
 *
 * Mirrors the Pydantic model in `backend/app/identity/agent_facts.py`. Field
 * casing at the JSON boundary is a deliberate mix: NANDA v1 keeps `agent_name`
 * and `verifiable_credentials` snake_cased, camelCases skill modes / doc url /
 * certification dates / proof fields, and uses hyphenated `x-massclaw` for
 * the MassClaw extension namespace. Do NOT "normalise" these — the backend
 * emits them exactly as declared here and downstream NANDA consumers rely on
 * the exact wire shape.
 */
export interface AgentFactsProvider {
  name: string;
  url: string;
  did?: string;
}

export interface AgentFactsAdaptiveResolver {
  url: string;
  policies?: string[];
}

export interface AgentFactsEndpoints {
  static: string[];
  adaptive_resolver?: AgentFactsAdaptiveResolver;
}

export interface AgentFactsAuthentication {
  methods: string[];
}

export interface AgentFactsCapabilities {
  modalities: string[];
  authentication: AgentFactsAuthentication;
}

export interface AgentFactsSkill {
  id: string;
  description: string;
  inputModes: string[];
  outputModes: string[];
  supportedLanguages?: string[];
  latencyBudgetMs?: number;
  maxTokens?: number;
}

export interface AgentFactsCertification {
  level: string;
  issuer?: string;
  issuedAt?: string;
  validUntil?: string;
}

export interface AgentFactsTelemetryMetrics {
  throughput_rps?: number;
  availability?: number;
  latency_p95_ms?: number;
}

export interface AgentFactsTelemetry {
  metrics: AgentFactsTelemetryMetrics;
}

export interface IntegrityProof {
  type: string;
  cryptosuite: string;
  created: string;
  verificationMethod: string;
  proofPurpose: string;
  proofValue: string;
}

export interface IntegrityCredential {
  type: string[];
  issuer: string;
  issued: string;
  proof: IntegrityProof;
}

export interface AgentFactsExampleRequest {
  [key: string]: unknown;
}

export interface AgentFactsExtensions {
  example_requests?: AgentFactsExampleRequest[];
  error_schema?: Record<string, unknown>;
  limits?: Record<string, unknown>;
  nanda_index_handle?: string;
}

export interface AgentFacts {
  id: string;
  agent_name: string;
  label: string;
  description: string;
  version: string;
  provider: AgentFactsProvider;
  endpoints: AgentFactsEndpoints;
  capabilities: AgentFactsCapabilities;
  skills: AgentFactsSkill[];
  documentationUrl?: string;
  jurisdiction?: string;
  certification?: AgentFactsCertification;
  telemetry?: AgentFactsTelemetry;
  evaluations?: unknown[];
  verifiable_credentials: IntegrityCredential[];
  "x-massclaw"?: AgentFactsExtensions;
}

export interface VerifyResult {
  valid: boolean;
  errors: string[];
  provider_did?: string;
  agent_id?: string;
  label?: string;
}
