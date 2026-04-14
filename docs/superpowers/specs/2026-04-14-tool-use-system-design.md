# MassClaw Tool Use System — Design Spec

## Context

MassClaw agents can only generate text. They can't browse the web, execute code, call APIs, or manipulate files. This is the #1 capability gap. This spec designs a production-grade tool use system that transforms agents from text generators into actors.

## Design Choices (User-Confirmed)

- **Hybrid execution**: In-process for lightweight tools, Docker sandboxes for code execution
- **4 built-in tools**: Web Search, Code Execution, File I/O, API Caller
- **Capability-based permissions**: Tools mapped to agent capabilities
- **Pluggable from day 1**: ToolProvider interface for built-in and third-party tools

---

## Architecture

### Core Data Structures

**`app/tools/base.py`**

```python
class ExecutionMode(str, Enum):
    IN_PROCESS = "in_process"
    SANDBOXED = "sandboxed"

@dataclass
class ToolContext:
    workflow_id: uuid.UUID
    agent_id: uuid.UUID
    workspace_path: str        # Per-workflow sandboxed directory
    timeout_seconds: int = 30
    max_iterations: int = 5

@dataclass
class ToolResult:
    content: str               # Human-readable result
    success: bool
    metadata: dict[str, Any]   # Tool-specific data
    artifacts: list[str]       # File paths created
    cost_credits: float = 0.0  # Cost of this execution

class ToolProvider(ABC):
    name: str
    description: str
    parameters_schema: dict          # JSON Schema for arguments
    execution_mode: ExecutionMode
    required_capabilities: set[str]  # Agent capabilities that grant access
    estimated_cost_credits: float

    @abstractmethod
    async def execute(self, arguments: dict, context: ToolContext) -> ToolResult: ...
```

### Capability-to-Tool Mapping

```
research, data-retrieval, literature-review  -> web_search, web_scrape
code-execution, optimization, data-analytics -> code_execute, file_read, file_write
process-analysis, workflow-mapping           -> file_read, file_write, api_call
cost-analysis, budget-estimation             -> file_read, api_call
verification, quality-check                  -> web_search, file_read
intake, classify, extract-requirements       -> (no tools — text-only)
summarization, report-generation             -> file_write
```

### Built-in Tools

| Tool | Execution Mode | Description |
|------|---------------|-------------|
| `web_search` | in_process | Brave Search API. Returns top results as structured text. |
| `web_scrape` | in_process | Fetch and parse web page content via httpx + BeautifulSoup. |
| `code_execute` | sandboxed | Run Python/JS/Shell in Docker. Capture stdout/stderr. 30s timeout, 256MB memory. |
| `file_read` | in_process | Read files from per-workflow workspace. Path traversal prevention. |
| `file_write` | in_process | Write files to per-workflow workspace. |
| `file_list` | in_process | List files in workflow workspace. |
| `api_call` | in_process | Authenticated HTTP requests. SSRF protection (block private IPs). |

### Tool Registry (`app/tools/registry.py`)

Singleton that manages available tools:
- `register(tool)` / `get(name)` / `list_tools()`
- `get_tools_for_capabilities(caps: list[str]) -> list[ToolProvider]`
- Auto-loads built-in tools on init
- Third-party tools register via the same `register()` API

### Tool Executor (`app/tools/executor.py`)

Service class (session, redis) that orchestrates tool execution:
1. Look up tool from registry
2. **Pre-execution**: validate arguments via `InjectionDetector.analyze()`
3. **Execute**: route to in-process or Docker based on `execution_mode`
4. **Post-execution**: filter results via `ContentFilter.analyze()`
5. **Audit**: log tool execution to audit service
6. **Cost**: track via wallet system
7. Return `ToolResult`

### Agentic Loop (Scheduler Integration)

Modified `_llm_call()` in `app/orchestration/scheduler.py`:

```
FUNCTION agentic_llm_call(node, agent, context):
    tools = registry.get_tools_for_capabilities(agent.capabilities)
    tool_schemas = [tool.to_schema() for tool in tools]
    
    messages = [initial_prompt]
    total_cost = Decimal(0)
    all_tool_calls = []
    
    FOR iteration IN range(max_iterations):
        response = model_router.generate(prompt=messages, tools=tool_schemas)
        total_cost += response.cost
        
        IF response.tool_calls IS NONE:
            RETURN response  # Final text response
        
        # Execute each tool call
        tool_results = []
        FOR call IN response.tool_calls:
            result = tool_executor.execute_tool(call.name, call.arguments, tool_context)
            tool_results.append(result)
            all_tool_calls.append({call, result})
        
        # Append tool results to conversation
        messages += format_tool_results(tool_results)
    
    # Max iterations reached — return last response with warning
    RETURN response
```

### Safety Integration

- **Pre-execution**: `InjectionDetector.analyze(str(arguments))` — block if `is_suspicious`
- **Post-execution**: `ContentFilter.redact_pii(result.content)` — redact sensitive data
- **Code execution**: Docker network=none (no internet), read-only filesystem except /tmp
- **File I/O**: `os.path.realpath()` validated against workspace root (prevent `../` traversal)
- **API caller**: Block private IPs (127.0.0.1, 10.x, 172.16-31.x, 192.168.x, 169.254.x)

### Configuration (`app/config.py` additions)

```python
# Tool System
tool_max_iterations: int = 5
tool_code_timeout_seconds: int = 30
tool_code_memory_mb: int = 256
tool_workspace_base: str = "/tmp/massclaw/workspaces"
brave_search_api_key: str = ""
tool_docker_image: str = "python:3.12-slim"
```

### API Endpoints (`app/api/tools.py`)

- `GET /api/v1/tools` — list all tools with schemas
- `GET /api/v1/tools/{name}` — tool details + JSON Schema
- `POST /api/v1/tools/{name}/test` — test-execute a tool (auth required)

### File Structure

```
app/tools/
    __init__.py
    base.py            # ToolProvider, ToolResult, ToolContext, ExecutionMode
    registry.py        # ToolRegistry singleton
    executor.py        # ToolExecutor service
    web_search.py      # WebSearchTool, WebScrapeTool
    code_execution.py  # CodeExecutionTool
    file_io.py         # FileReadTool, FileWriteTool, FileListTool
    api_caller.py      # APICallerTool
```

---

## Implementation Phases

### Phase 1: Foundation
1. `app/tools/__init__.py` — empty package
2. `app/tools/base.py` — ToolProvider, ToolContext, ToolResult, ExecutionMode
3. `app/config.py` — add tool settings
4. `app/exceptions.py` — add ToolExecutionError

### Phase 2: Built-in Tools (parallelizable)
5. `app/tools/file_io.py` — FileRead, FileWrite, FileList
6. `app/tools/web_search.py` — WebSearch, WebScrape
7. `app/tools/api_caller.py` — APICaller
8. `app/tools/code_execution.py` — CodeExecution (requires aiodocker)

### Phase 3: Infrastructure
9. `app/tools/registry.py` — ToolRegistry
10. `app/tools/executor.py` — ToolExecutor
11. `pyproject.toml` — add aiodocker, beautifulsoup4 dependencies

### Phase 4: Integration
12. `app/orchestration/scheduler.py` — agentic loop in `_llm_call()`
13. `app/dependencies.py` — add `get_tool_executor()`
14. `app/api/tools.py` — API endpoints
15. `app/api/router.py` — mount tools router

### Phase 5: Finalization
16. `app/main.py` — tool registry init in lifespan
17. `.env.example` — document new settings
18. Tests for all tools and the agentic loop

## Verification

1. `ruff check app/tools/` — lint clean
2. `python -c "from app.tools.registry import get_tool_registry; r = get_tool_registry(); print(r.list_tools())"` — tools register
3. `pytest tests/unit/test_tools.py -v` — tool unit tests pass
4. Manual: submit workflow with research task, verify web_search tool is called
5. Manual: submit workflow with code task, verify Docker sandbox executes
6. `GET /api/v1/tools` — returns all 7 tools with schemas

## Challenges & Mitigations

- **Multi-turn format**: Current `generate()` takes `prompt: str`. Use prompt stuffing to append tool results. Proper `messages: list` refactor can follow.
- **Docker availability**: CodeExecutionTool checks Docker at runtime, returns clear error if unavailable.
- **Workspace cleanup**: `shutil.rmtree(workspace)` in scheduler finalization after workflow completes.
- **SSRF**: Block private IPs + link-local in API caller.
