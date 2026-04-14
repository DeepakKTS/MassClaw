"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Brain, Search, Loader2 } from "lucide-react";

export default function MemoryPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    try {
      const data = await api.post<any[]>("/memory/query", {
        query: query.trim(),
        min_similarity: 0.3,
        top_k: 10,
      });
      setResults(data);
    } catch (err) {
      console.error(err);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading">Memory Explorer</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">Semantic search across agent shared memory</p>
      </div>

      <form onSubmit={handleSearch} className="flex gap-3">
        <div className="flex-1 relative">
          <Search size={16} className="absolute left-3 top-3 text-massclaw-text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search memories semantically..."
            className="w-full glass rounded-xl pl-10 pr-4 py-2.5 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-massclaw-accent/50 border-none"
          />
        </div>
        <button
          type="submit"
          disabled={searching}
          className="px-5 py-2 bg-massclaw-accent hover:bg-massclaw-accent-light text-white rounded-xl text-sm font-medium transition disabled:opacity-50"
        >
          {searching ? <Loader2 size={16} className="animate-spin" /> : "Search"}
        </button>
      </form>

      <div className="space-y-3">
        {results.map((r, i) => (
          <div key={i} className="glass rounded-xl p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 uppercase tracking-wider">
                {r.memory?.memory_type}
              </span>
              <div className="text-[11px] text-massclaw-text-muted font-mono">
                {(r.similarity * 100).toFixed(1)}% match &middot; {(r.relevance_score * 100).toFixed(1)}% relevant
              </div>
            </div>
            <p className="text-sm whitespace-pre-wrap leading-relaxed">{r.memory?.content}</p>
          </div>
        ))}
        {results.length === 0 && query && !searching && (
          <p className="text-massclaw-text-muted text-center py-12 text-sm">No results found</p>
        )}
      </div>
    </div>
  );
}
