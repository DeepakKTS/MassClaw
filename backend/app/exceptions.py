from __future__ import annotations


class MassClawError(Exception):
    """Base exception for all MassClaw errors."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, detail: str = "An internal error occurred", **kwargs: object) -> None:
        self.detail = detail
        self.extra = kwargs
        super().__init__(detail)


class NotFoundError(MassClawError):
    status_code = 404
    error_code = "NOT_FOUND"

    def __init__(self, resource: str = "Resource", identifier: str = "") -> None:
        detail = f"{resource} not found"
        if identifier:
            detail = f"{resource} '{identifier}' not found"
        super().__init__(detail=detail, resource=resource, identifier=identifier)


class ConflictError(MassClawError):
    status_code = 409
    error_code = "CONFLICT"

    def __init__(self, detail: str = "Resource conflict") -> None:
        super().__init__(detail=detail)


class ValidationError(MassClawError):
    status_code = 422
    error_code = "VALIDATION_ERROR"

    def __init__(self, detail: str = "Validation failed", errors: list[dict] | None = None) -> None:
        self.errors = errors or []
        super().__init__(detail=detail)


class AuthenticationError(MassClawError):
    status_code = 401
    error_code = "AUTHENTICATION_FAILED"

    def __init__(self, detail: str = "Authentication required") -> None:
        super().__init__(detail=detail)


class AuthorizationError(MassClawError):
    status_code = 403
    error_code = "FORBIDDEN"

    def __init__(self, detail: str = "Insufficient permissions") -> None:
        super().__init__(detail=detail)


class RateLimitError(MassClawError):
    status_code = 429
    error_code = "RATE_LIMITED"

    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after
        super().__init__(detail=f"Rate limit exceeded. Retry after {retry_after} seconds.")


class BudgetExhaustedError(MassClawError):
    status_code = 402
    error_code = "BUDGET_EXHAUSTED"

    def __init__(self, detail: str = "Workflow budget exhausted") -> None:
        super().__init__(detail=detail)


class AgentUnavailableError(MassClawError):
    status_code = 503
    error_code = "AGENT_UNAVAILABLE"

    def __init__(self, detail: str = "No suitable agent available") -> None:
        super().__init__(detail=detail)


class PolicyViolationError(MassClawError):
    status_code = 403
    error_code = "POLICY_VIOLATION"

    def __init__(self, detail: str = "Policy violation", rule_name: str = "") -> None:
        self.rule_name = rule_name
        super().__init__(detail=detail, rule_name=rule_name)


class CircuitBreakerOpenError(MassClawError):
    status_code = 503
    error_code = "CIRCUIT_BREAKER_OPEN"

    def __init__(self, service: str = "Service") -> None:
        super().__init__(detail=f"{service} circuit breaker is open — service temporarily unavailable")


class OrchestrationError(MassClawError):
    status_code = 500
    error_code = "ORCHESTRATION_ERROR"

    def __init__(self, detail: str = "Workflow orchestration failed") -> None:
        super().__init__(detail=detail)


class DAGValidationError(MassClawError):
    status_code = 422
    error_code = "DAG_VALIDATION_ERROR"

    def __init__(self, detail: str = "Invalid task dependency graph") -> None:
        super().__init__(detail=detail)
