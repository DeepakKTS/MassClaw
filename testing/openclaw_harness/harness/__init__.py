"""OpenClaw hardening harness — local contract-test lab for MassClaw.

State + telemetry only. This package never calls an LLM directly.
All simulated-agent work happens in the main Claude Code session via subagents,
which POST their transcripts and verdicts back to the server in this package.
"""
