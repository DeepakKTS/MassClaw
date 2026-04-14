from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config import get_settings
from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

_LOG_CAP = 10_000

_SUPPORTED_LANGUAGES: dict[str, dict[str, str]] = {
    "python": {
        "filename": "script.py",
        "run_cmd": "python /tmp/script.py",
    },
    "javascript": {
        "filename": "script.js",
        "run_cmd": "node /tmp/script.js",
    },
    "shell": {
        "filename": "script.sh",
        "run_cmd": "sh /tmp/script.sh",
    },
}


class CodeExecutionTool(ToolProvider):
    name = "code_execute"
    description = (
        "Execute Python, JavaScript, or Shell code inside an isolated Docker container "
        "with no network access. Returns stdout/stderr output capped at 10 000 characters."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "description": "Language to execute: 'python', 'javascript', or 'shell'.",
                "enum": ["python", "javascript", "shell"],
                "default": "python",
            },
            "code": {
                "type": "string",
                "description": "The source code to execute.",
            },
        },
        "required": ["code"],
    }
    execution_mode = ExecutionMode.SANDBOXED
    required_capabilities: set[str] = {
        "code-execution",
        "optimization",
        "data-analytics",
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        # Guard: aiodocker availability
        try:
            import aiodocker  # type: ignore[import-untyped]
        except ImportError:
            return ToolResult(
                content=("Error: 'aiodocker' is not installed. Install it with: pip install 'aiodocker>=0.23,<1.0'"),
                success=False,
            )

        code: str = arguments.get("code", "").strip()
        if not code:
            return ToolResult(content="Error: 'code' argument must not be empty.", success=False)

        language: str = arguments.get("language", "python").lower()
        if language not in _SUPPORTED_LANGUAGES:
            return ToolResult(
                content=f"Error: unsupported language '{language}'. Supported: {', '.join(sorted(_SUPPORTED_LANGUAGES))}.",
                success=False,
            )

        settings = get_settings()
        timeout: int = settings.tool_code_timeout_seconds
        memory_mb: int = settings.tool_code_memory_mb
        docker_image: str = settings.tool_docker_image
        cpu_quota: int = settings.tool_code_cpu_quota
        disk_mb: int = settings.tool_code_disk_mb

        lang_cfg = _SUPPORTED_LANGUAGES[language]
        filename: str = lang_cfg["filename"]
        run_cmd: str = lang_cfg["run_cmd"]

        # Encode the code as a base64-injected inline command so we don't need a
        # bind mount. We write the file to /tmp inside the container, then execute it.
        import base64

        code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
        shell_cmd = f"echo {code_b64} | base64 -d > /tmp/{filename} && {run_cmd}"

        container_config = {
            "Image": docker_image,
            "Cmd": ["/bin/sh", "-c", shell_cmd],
            "NetworkDisabled": True,
            "WorkingDir": "/tmp",
            "HostConfig": {
                "NetworkMode": "none",
                "Memory": memory_mb * 1024 * 1024,
                "MemorySwap": memory_mb * 1024 * 1024,  # disable swap
                "PidsLimit": 64,
                "CpuQuota": cpu_quota,  # Default 50000 = 50% of one core
                "CpuPeriod": 100000,
                "Tmpfs": {"/tmp": f"size={disk_mb}m,noexec=false"},
            },
        }

        try:
            docker = aiodocker.Docker()
        except Exception as e:
            return ToolResult(
                content=f"Docker unavailable: {e}. Ensure Docker is running.",
                success=False,
                metadata={"language": language, "error": "docker_unavailable"},
            )

        container = None
        start_time = time.time()
        try:
            container = await docker.containers.create(config=container_config)
            await container.start()

            try:
                wait_task = asyncio.ensure_future(container.wait())
                await asyncio.wait_for(wait_task, timeout=float(timeout))
            except TimeoutError:
                try:
                    await container.kill()
                except Exception:
                    pass
                return ToolResult(
                    content=f"Error: code execution timed out after {timeout}s.",
                    success=False,
                    metadata={"language": language, "timeout_seconds": timeout},
                )

            # Collect logs (stdout + stderr merged)
            log_chunks: list[str] = []
            async for chunk in container.log(stdout=True, stderr=True):
                log_chunks.append(chunk)

            output = "".join(log_chunks)
            truncated = len(output) > _LOG_CAP
            if truncated:
                output = output[:_LOG_CAP] + "\n…(output truncated)"

            # Retrieve exit code
            end_time = time.time()
            container_info = await container.show()
            exit_code: int = container_info["State"]["ExitCode"]
            succeeded = exit_code == 0
            execution_time_ms = int((end_time - start_time) * 1000)

            return ToolResult(
                content=output if output else "(no output)",
                success=succeeded,
                metadata={
                    "language": language,
                    "exit_code": exit_code,
                    "truncated": truncated,
                    "docker_image": docker_image,
                    "execution_time_ms": execution_time_ms,
                },
            )

        except aiodocker.exceptions.DockerError as exc:
            return ToolResult(
                content=f"Error: Docker error — {exc}",
                success=False,
                metadata={"language": language},
            )
        finally:
            if container is not None:
                try:
                    await container.delete(force=True)
                except Exception:
                    pass
            try:
                await docker.close()
            except Exception:
                pass
