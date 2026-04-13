from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from decimal import Decimal

from app.core.logging import get_logger
from app.llm.base import LLMChunk, LLMProvider, LLMResponse

logger = get_logger(__name__)

# Realistic mock responses keyed by capability
MOCK_DECOMPOSITION = {
    "domain": "healthcare-operations",
    "tasks": [
        {
            "id": "t1",
            "capability": "intake",
            "description": "Parse the user request and extract the operational scope, key constraints, desired outcomes, and stakeholders involved in the healthcare workflow analysis.",
            "depends_on": [],
            "estimated_complexity": "low",
        },
        {
            "id": "t2",
            "capability": "research",
            "description": "Research industry benchmarks, best practices, and relevant metrics for the identified healthcare operational area. Include wait time standards, staffing ratios, and throughput benchmarks.",
            "depends_on": ["t1"],
            "estimated_complexity": "medium",
        },
        {
            "id": "t3",
            "capability": "process-analysis",
            "description": "Analyze the current workflow to identify bottlenecks, resource constraints, and inefficiencies. Map the patient flow and identify critical path delays.",
            "depends_on": ["t1", "t2"],
            "estimated_complexity": "high",
        },
        {
            "id": "t4",
            "capability": "risk-assessment",
            "description": "Identify regulatory, patient safety, compliance, and operational risks associated with current processes and any proposed changes.",
            "depends_on": ["t3"],
            "estimated_complexity": "medium",
        },
        {
            "id": "t5",
            "capability": "optimization",
            "description": "Propose concrete scheduling improvements, staffing changes, and resource reallocation strategies based on the bottleneck analysis.",
            "depends_on": ["t3"],
            "estimated_complexity": "high",
        },
        {
            "id": "t6",
            "capability": "cost-analysis",
            "description": "Estimate the financial impact of proposed changes, including implementation costs, projected savings, and ROI timeline.",
            "depends_on": ["t3"],
            "estimated_complexity": "medium",
        },
        {
            "id": "t7",
            "capability": "summarization",
            "description": "Synthesize all findings from risk assessment, optimization proposals, and cost analysis into a comprehensive executive operations brief.",
            "depends_on": ["t4", "t5", "t6"],
            "estimated_complexity": "medium",
        },
        {
            "id": "t8",
            "capability": "verification",
            "description": "Cross-check all outputs for logical consistency, verify that recommendations align with identified risks, and ensure completeness of the analysis.",
            "depends_on": ["t7"],
            "estimated_complexity": "medium",
        },
    ],
}

MOCK_AGENT_OUTPUTS: dict[str, str] = {
    "intake": (
        "## Intake Analysis\n\n"
        "**Operational Area:** Outpatient department workflow optimization\n"
        "**Key Constraints:** Current staffing levels, physical space limitations, existing IT systems\n"
        "**Desired Outcomes:** Reduce patient wait times, improve throughput, optimize staff utilization\n"
        "**Stakeholders:** Department heads, nursing staff, registration clerks, patients, IT department\n\n"
        "The request focuses on analyzing bottlenecks in the outpatient registration and processing pipeline, "
        "with emphasis on scheduling improvements and staffing efficiency."
    ),
    "research": (
        "## Research Findings\n\n"
        "**Industry Benchmarks:**\n"
        "- Best-in-class outpatient registration: < 8 minutes per patient\n"
        "- Optimal patient-to-staff ratio: 15:1 during peak hours\n"
        "- Digital check-in adoption reduces wait times by 40-60%\n"
        "- Pre-registration via patient portal reduces in-person time by 70%\n\n"
        "**Key Metrics:**\n"
        "- Average daily patient volume: 400-500 patients\n"
        "- Peak hour throughput target: 80 patients/hour\n"
        "- Industry average walkaway rate at 30+ min wait: 8-12%\n"
        "- Each walkaway = $350 average lost revenue"
    ),
    "process-analysis": (
        "## Process Analysis\n\n"
        "**Identified Bottlenecks:**\n"
        "1. **Registration Desk** (Critical): 35-minute average wait. 3 clerks handling 60 patients/hour peak.\n"
        "2. **Lab Specimen Collection**: 22-minute average wait. 2 phlebotomists during peak.\n"
        "3. **Insurance Verification**: Manual process adding 8 minutes per patient.\n\n"
        "**Root Causes:**\n"
        "- Hourly block scheduling creates arrival surges\n"
        "- No digital pre-registration pathway\n"
        "- Manual insurance verification for 65% of patients\n"
        "- Single queue model creates perceived longer waits"
    ),
    "risk-assessment": (
        "## Risk Assessment\n\n"
        "| Risk | Severity | Likelihood | Mitigation |\n"
        "|------|----------|------------|------------|\n"
        "| Patient walkaway due to long waits | High | High | Implement staggered scheduling |\n"
        "| HIPAA compliance gaps in manual registration | Medium | Medium | Audit current processes |\n"
        "| Staff burnout during peak hours | Medium | High | Redistribute workload |\n"
        "| IT system downtime affecting digital check-in | Low | Low | Redundancy planning |\n\n"
        "**Compliance Note:** Current manual registration process requires HIPAA audit for patient data handling."
    ),
    "optimization": (
        "## Optimization Proposals\n\n"
        "**1. Staggered Appointment Scheduling**\n"
        "- Switch from hourly blocks to 15-minute intervals\n"
        "- Expected: 40% reduction in peak queue depth\n\n"
        "**2. Self-Service Kiosks**\n"
        "- Deploy 2 check-in kiosks for returning patients\n"
        "- Expected: Handle 30% of registrations, freeing clerk capacity\n\n"
        "**3. Pre-Registration Phone Line**\n"
        "- Shift 1 clerk to pre-registration during off-peak (10am-2pm)\n"
        "- Expected: 25% of next-day patients pre-registered\n\n"
        "**4. Express Lab Lane**\n"
        "- Dedicated phlebotomist for routine draws\n"
        "- Expected: 15-minute reduction in lab wait times"
    ),
    "cost-analysis": (
        "## Cost Analysis\n\n"
        "**Implementation Costs:**\n"
        "- Self-service kiosks (2 units): $15,000\n"
        "- Scheduling software upgrade: $8,000\n"
        "- Staff training: $3,000\n"
        "- Total upfront: $26,000\n\n"
        "**Projected Annual Savings:**\n"
        "- Reduced walkaway revenue loss: $180,000/year\n"
        "- Improved staff utilization: $45,000/year\n"
        "- Faster throughput enabling +20 patients/day: $140,000/year\n"
        "- Total savings: $365,000/year\n\n"
        "**ROI:** 14x first-year return. Payback period: 26 days."
    ),
    "summarization": (
        "## Executive Operations Brief\n\n"
        "### Summary\n"
        "Analysis of the outpatient department reveals three critical bottlenecks: registration (35-min wait), "
        "lab collection (22-min wait), and insurance verification (8-min delay). These result in an estimated "
        "8-12% patient walkaway rate, translating to ~$630,000 annual revenue loss.\n\n"
        "### Recommended Actions\n"
        "1. Implement staggered 15-minute appointment intervals (40% queue reduction)\n"
        "2. Deploy 2 self-service check-in kiosks ($15K investment)\n"
        "3. Create pre-registration phone line during off-peak hours\n"
        "4. Establish express lab lane for routine draws\n\n"
        "### Financial Impact\n"
        "- Investment: $26,000 one-time\n"
        "- Annual savings: $365,000\n"
        "- ROI: 14x first year\n\n"
        "### Risk Considerations\n"
        "- HIPAA audit recommended before digital check-in rollout\n"
        "- Staff retraining needed for new scheduling system\n"
        "- Phased rollout recommended to minimize disruption"
    ),
    "verification": (
        "## Verification Report\n\n"
        "**Consistency Check:** PASS\n"
        "- Financial figures are internally consistent across cost and optimization analyses\n"
        "- Risk mitigations align with proposed optimization strategies\n"
        "- Staffing recommendations are feasible within current headcount\n\n"
        "**Completeness Check:** PASS\n"
        "- All identified bottlenecks have corresponding improvement proposals\n"
        "- Cost estimates include both upfront and ongoing considerations\n"
        "- Compliance risks are identified with actionable mitigations\n\n"
        "**Issues Found:** None critical. Minor note: lab express lane staffing "
        "may need seasonal adjustment during flu season."
    ),
}

# Fallback for capabilities not explicitly mapped
DEFAULT_MOCK_OUTPUT = (
    "## Analysis Complete\n\n"
    "Based on the provided context and task requirements, here are the key findings:\n\n"
    "1. The analysis has been completed according to specifications.\n"
    "2. Key metrics and data points have been evaluated.\n"
    "3. Recommendations are aligned with the overall workflow objectives.\n\n"
    "Detailed findings are consistent with prior agent outputs and support the workflow goals."
)

MOCK_SYNTHESIS = (
    "## MassClaw Workflow Synthesis\n\n"
    "### Comprehensive Healthcare Operations Analysis\n\n"
    "This report synthesizes findings from 8 specialized AI agents analyzing outpatient department operations.\n\n"
    "#### Key Findings\n"
    "- Registration bottleneck: 35-minute average wait impacting 450 daily patients\n"
    "- Lab collection delay: 22-minute wait as secondary bottleneck\n"
    "- Estimated 8-12% walkaway rate = $630K annual revenue loss\n\n"
    "#### Recommended Interventions\n"
    "1. **Staggered scheduling** (15-min intervals): 40% queue reduction\n"
    "2. **Self-service kiosks** (2 units, $15K): Handle 30% of registrations\n"
    "3. **Pre-registration phone line**: 25% next-day pre-registration rate\n"
    "4. **Express lab lane**: 15-min reduction in lab waits\n\n"
    "#### Financial Summary\n"
    "- Investment: $26,000 | Annual savings: $365,000 | ROI: 14x Year 1\n\n"
    "#### Verified\n"
    "All outputs cross-checked for consistency. No critical issues found."
)


class MockProvider(LLMProvider):
    """Mock LLM provider for testing the full orchestration pipeline.

    Returns realistic, domain-specific responses without making API calls.
    Simulates realistic latency and token counts.
    """

    provider_name = "mock"

    def default_model(self) -> str:
        return "mock-model-v1"

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """Return a realistic mock response based on the prompt content."""
        start = self._start_timer()

        # Simulate realistic latency (200-800ms)
        await asyncio.sleep(random.uniform(0.2, 0.8))

        content = self._select_response(prompt, system)
        input_tokens = self.count_tokens(prompt + (system or ""))
        output_tokens = self.count_tokens(content)

        latency_ms = self._elapsed_ms(start)

        result = LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model or "mock-model-v1",
            latency_ms=round(latency_ms, 2),
            cost=Decimal("0.001"),  # Nominal cost for wallet testing
            metadata={"provider": "mock"},
        )

        logger.info(
            "mock_llm_call",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency_ms, 1),
        )

        return result

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[LLMChunk]:
        """Stream mock response in chunks."""
        content = self._select_response(prompt, system)
        words = content.split()

        for i in range(0, len(words), 5):
            chunk = " ".join(words[i : i + 5]) + " "
            await asyncio.sleep(0.05)
            yield LLMChunk(content=chunk)

        yield LLMChunk(content="", is_final=True)

    def _select_response(self, prompt: str, system: str | None) -> str:
        """Select the appropriate mock response based on prompt content."""
        prompt_lower = prompt.lower()
        system_lower = (system or "").lower()
        combined = prompt_lower + " " + system_lower

        # 0. Goal interpretation prompt
        if "structured goal" in prompt_lower or "extract a structured goal" in prompt_lower or ("intent" in prompt_lower and "risk_level" in prompt_lower and "complexity" in prompt_lower):
            return json.dumps({
                "intent": "analyze",
                "constraints": ["staffing levels", "budget limitations"],
                "risk_level": "medium",
                "expected_output_type": "report",
                "complexity_estimate": "complex",
                "domain_hint": "healthcare-operations",
                "stop_conditions": ["comprehensive report generated"],
            })

        # 1. Match by system prompt identity (most reliable)
        if "task decomposition engine" in system_lower or "decompose" in system_lower:
            return json.dumps(MOCK_DECOMPOSITION, indent=2)

        if "output synthesizer" in system_lower or ("synthesize" in prompt_lower and "agent outputs" in prompt_lower):
            return MOCK_SYNTHESIS

        # 1b. Reflection prompt
        if "quality evaluator" in prompt_lower or ("evaluate the outputs" in prompt_lower and "next action" in prompt_lower):
            return json.dumps({
                "should_continue": False,
                "confidence": 0.85,
                "issues": [],
                "suggestions": [],
                "action": "accept",
            })

        # 2. Fallback: keyword matching on prompt only
        if "decompose" in prompt_lower and "subtask" in prompt_lower:
            return json.dumps(MOCK_DECOMPOSITION, indent=2)

        # Match by capability keywords
        for capability, output in MOCK_AGENT_OUTPUTS.items():
            cap_words = capability.replace("-", " ").split()
            if any(w in prompt_lower for w in cap_words):
                return output

        # Check system prompt for capability hints
        if system:
            system_lower = system.lower()
            for capability, output in MOCK_AGENT_OUTPUTS.items():
                cap_words = capability.replace("-", " ").split()
                if any(w in system_lower for w in cap_words):
                    return output

        return DEFAULT_MOCK_OUTPUT
