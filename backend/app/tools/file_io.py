from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.tools.base import ExecutionMode, ToolContext, ToolProvider, ToolResult

_FILE_READ_CAP = 1 * 1024 * 1024  # 1 MB


def _safe_resolve(workspace: str, relative_path: str) -> Path | None:
    """Resolve *relative_path* inside *workspace*, returning None if the resolved
    path escapes the workspace root (path-traversal guard)."""
    # Reject null bytes, control characters, and excessively long paths
    if "\x00" in relative_path or len(relative_path) > 1024:
        return None
    if any(ord(c) < 32 and c not in ("\n", "\r", "\t") for c in relative_path):
        return None
    workspace_real = os.path.realpath(workspace)
    raw_path = os.path.join(workspace, relative_path)
    # Block symlinks — they could point outside the workspace
    if os.path.islink(raw_path):
        return None
    candidate = os.path.realpath(raw_path)
    if not candidate.startswith(workspace_real + os.sep) and candidate != workspace_real:
        return None
    return Path(candidate)


class FileReadTool(ToolProvider):
    name = "file_read"
    description = (
        "Read the contents of a file from the agent workspace. "
        "The path must be relative to the workspace root. Files larger than 1 MB are rejected."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file inside the workspace.",
            },
        },
        "required": ["path"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities: set[str] = {
        "research",
        "code-execution",
        "verification",
        "quality-check",
        "report-generation",
        "summarization",
        "data-analytics",
        "literature-review",
        "data-retrieval",
        "process-analysis",
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path_arg: str = arguments.get("path", "").strip()
        if not path_arg:
            return ToolResult(content="Error: 'path' argument is required.", success=False)

        resolved = _safe_resolve(context.workspace_path, path_arg)
        if resolved is None:
            return ToolResult(
                content=f"Error: path '{path_arg}' escapes the workspace root — path traversal is not allowed.",
                success=False,
            )

        if not resolved.exists():
            return ToolResult(content=f"Error: file '{path_arg}' does not exist.", success=False)

        if not resolved.is_file():
            return ToolResult(content=f"Error: '{path_arg}' is not a file.", success=False)

        size = resolved.stat().st_size
        if size > _FILE_READ_CAP:
            return ToolResult(
                content=f"Error: file '{path_arg}' is {size} bytes, exceeding the 1 MB read cap.",
                success=False,
                metadata={"size_bytes": size},
            )

        # Detect binary files — check first 1024 bytes for null byte
        with open(resolved, "rb") as bf:
            chunk = bf.read(1024)
            if b"\x00" in chunk:
                return ToolResult(
                    content=f"Error: file '{path_arg}' appears to be binary and cannot be read as text.",
                    success=False,
                    metadata={"path": path_arg, "binary": True},
                )

        content = resolved.read_text(encoding="utf-8", errors="replace")
        return ToolResult(
            content=content,
            success=True,
            metadata={"path": path_arg, "size_bytes": size},
        )


class FileWriteTool(ToolProvider):
    name = "file_write"
    description = (
        "Write text content to a file in the agent workspace. "
        "The path must be relative to the workspace root. Parent directories are created automatically."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the destination file inside the workspace.",
            },
            "content": {
                "type": "string",
                "description": "Text content to write to the file.",
            },
        },
        "required": ["path", "content"],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities: set[str] = {
        "code-execution",
        "summarization",
        "report-generation",
        "data-analytics",
        "optimization",
        "workflow-mapping",
        "process-analysis",
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path_arg: str = arguments.get("path", "").strip()
        if not path_arg:
            return ToolResult(content="Error: 'path' argument is required.", success=False)

        content: str = arguments.get("content") or ""

        resolved = _safe_resolve(context.workspace_path, path_arg)
        if resolved is None:
            return ToolResult(
                content=f"Error: path '{path_arg}' escapes the workspace root — path traversal is not allowed.",
                success=False,
            )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return ToolResult(
            content=f"File '{path_arg}' written successfully ({len(content)} characters).",
            success=True,
            metadata={"path": path_arg, "bytes_written": len(content.encode("utf-8"))},
            artifacts=[str(resolved)],
        )


class FileListTool(ToolProvider):
    name = "file_list"
    description = (
        "List files and directories inside the agent workspace (or a sub-directory of it). "
        "Returns a newline-separated list of relative paths."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Relative path of the sub-directory to list. Defaults to the workspace root when omitted or empty."
                ),
                "default": "",
            },
            "recursive": {
                "type": "boolean",
                "description": "If true, list all files recursively in subdirectories.",
                "default": False,
            },
        },
        "required": [],
    }
    execution_mode = ExecutionMode.IN_PROCESS
    required_capabilities: set[str] = {
        "research",
        "code-execution",
        "verification",
        "workflow-mapping",
        "process-analysis",
        "data-analytics",
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path_arg: str = arguments.get("path", "").strip()

        if path_arg:
            resolved = _safe_resolve(context.workspace_path, path_arg)
            if resolved is None:
                return ToolResult(
                    content=f"Error: path '{path_arg}' escapes the workspace root — path traversal is not allowed.",
                    success=False,
                )
        else:
            resolved = Path(os.path.realpath(context.workspace_path))

        if not resolved.exists():
            return ToolResult(
                content=f"Error: directory '{path_arg or '.'}' does not exist.",
                success=False,
            )

        if not resolved.is_dir():
            return ToolResult(
                content=f"Error: '{path_arg}' is not a directory.",
                success=False,
            )

        workspace_real = Path(os.path.realpath(context.workspace_path))
        recursive = bool(arguments.get("recursive", False))
        entries: list[str] = []

        if recursive:
            for root, dirs, files in os.walk(resolved):
                root_path = Path(root)
                for d in sorted(dirs):
                    entries.append(str((root_path / d).relative_to(workspace_real)) + "/")
                for f in sorted(files):
                    entries.append(str((root_path / f).relative_to(workspace_real)))
        else:
            for entry in sorted(resolved.iterdir()):
                rel = entry.relative_to(workspace_real)
                suffix = "/" if entry.is_dir() else ""
                entries.append(str(rel) + suffix)

        if not entries:
            return ToolResult(
                content="(empty directory)",
                success=True,
                metadata={"path": path_arg or ".", "count": 0},
            )

        return ToolResult(
            content="\n".join(entries),
            success=True,
            metadata={"path": path_arg or ".", "count": len(entries)},
        )
