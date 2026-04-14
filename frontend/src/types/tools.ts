export interface Tool {
  name: string;
  description: string;
  execution_mode: "in_process" | "sandboxed";
  required_capabilities: string[];
  estimated_cost_credits: number;
  parameters: Record<string, unknown>;
  schema?: Record<string, unknown>;
}

export interface MCPServer {
  name: string;
  command: string;
  connected: boolean;
  tools_registered?: string[];
}
