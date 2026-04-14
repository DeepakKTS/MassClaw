"use client";

import { Zap, Server, CircleDot, Terminal, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMCPServers } from "@/hooks/useTools";
import type { MCPServer } from "@/types/tools";

function ServerCard({ server }: { server: MCPServer }) {
  return (
    <div className="glass rounded-xl p-5 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Server size={18} className="text-massclaw-accent" />
          <h3 className="font-semibold text-sm">{server.name}</h3>
        </div>
        <span className="flex items-center gap-1.5 text-xs">
          <CircleDot
            size={10}
            className={cn(server.connected ? "text-massclaw-success" : "text-massclaw-danger")}
          />
          {server.connected ? "Connected" : "Disconnected"}
        </span>
      </div>
      <div className="text-xs font-mono text-massclaw-text-muted bg-massclaw-bg/60 rounded px-2 py-1">
        {server.command} {server.tools_registered ? `(${server.tools_registered.length} tools)` : ""}
      </div>
      {server.tools_registered && server.tools_registered.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {server.tools_registered.map((tool) => (
            <span
              key={tool}
              className="text-[10px] px-2 py-0.5 rounded-full bg-massclaw-accent/10 text-massclaw-accent"
            >
              {tool}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function MCPPage() {
  const { data: servers, isLoading, error } = useMCPServers();

  const connectedCount = servers?.filter((s) => s.connected).length || 0;

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading flex items-center gap-2">
          <Zap size={24} className="text-massclaw-accent" />
          MCP Servers
        </h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">
          Connect external tool servers via the Model Context Protocol. Discovered tools are automatically available to agents.
        </p>
      </div>

      {error && (
        <div className="glass rounded-xl p-4 border border-massclaw-danger/20 text-massclaw-danger text-sm">
          Failed to load MCP servers: {error.message}
        </div>
      )}

      {isLoading && (
        <div className="glass rounded-xl p-8 text-center text-massclaw-text-muted">Loading...</div>
      )}

      {servers && servers.length > 0 && (
        <div className="glass rounded-xl p-4">
          <div className="grid grid-cols-2 gap-4 text-center">
            <div>
              <div className="text-2xl font-bold text-massclaw-accent">{servers.length}</div>
              <div className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">Registered</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-massclaw-success">{connectedCount}</div>
              <div className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">Connected</div>
            </div>
          </div>
        </div>
      )}

      {servers && servers.length > 0 ? (
        <div className="space-y-3">
          {servers.map((server) => (
            <ServerCard key={server.name} server={server} />
          ))}
        </div>
      ) : (
        !isLoading && (
          <div className="glass rounded-xl p-6 space-y-4">
            <div className="text-center space-y-2">
              <Server size={32} className="text-massclaw-text-muted mx-auto" />
              <h3 className="font-semibold">No MCP Servers Connected</h3>
              <p className="text-sm text-massclaw-text-muted max-w-md mx-auto">
                Connect external tool servers to give agents access to GitHub, Slack, databases, file systems, and more.
              </p>
            </div>

            <div className="space-y-2">
              <h4 className="text-xs font-semibold text-massclaw-text-muted uppercase tracking-wider flex items-center gap-1">
                <Terminal size={12} />
                Register via API
              </h4>
              <pre className="text-[11px] font-mono bg-massclaw-bg/60 rounded-lg p-4 overflow-x-auto text-massclaw-text-muted border border-white/[0.04]">
{`POST /api/v1/mcp/servers
{
  "name": "github",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": { "GITHUB_TOKEN": "ghp_..." }
}`}
              </pre>
            </div>

            <div className="space-y-2">
              <h4 className="text-xs font-semibold text-massclaw-text-muted uppercase tracking-wider flex items-center gap-1">
                <ExternalLink size={12} />
                Popular MCP Servers
              </h4>
              <div className="grid gap-2 text-xs">
                {[
                  { name: "Filesystem", pkg: "@modelcontextprotocol/server-filesystem", desc: "Read/write local files" },
                  { name: "GitHub", pkg: "@modelcontextprotocol/server-github", desc: "Repos, issues, PRs" },
                  { name: "PostgreSQL", pkg: "@modelcontextprotocol/server-postgres", desc: "Query databases" },
                  { name: "Slack", pkg: "@modelcontextprotocol/server-slack", desc: "Send/read messages" },
                ].map((s) => (
                  <div key={s.name} className="flex items-center justify-between bg-white/[0.03] rounded-lg px-3 py-2">
                    <div>
                      <span className="font-medium">{s.name}</span>
                      <span className="text-massclaw-text-muted ml-2">{s.desc}</span>
                    </div>
                    <code className="text-[10px] text-massclaw-accent">{s.pkg}</code>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )
      )}
    </div>
  );
}
