"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Brain, Search } from "lucide-react";

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
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Memory Explorer</h1>
        <p className="text-massclaw-text-muted mt-1">Semantic search across agent shared memory</p>
      </div>

      <form onSubmit={handleSearch} className="flex gap-3">
        <div className="flex-1 relative">
          <Search size={16} className="absolute left-3 top-3 text-massclaw-text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search memories semantically..."
            className="w-full bg-massclaw-surface border border-massclaw-border rounded-lg pl-10 pr-4 py-2.5 text-sm focus:outline-none focus:border-massclaw-accent"
          />
        </div>
        <button
          type="submit"
          disabled={searching}
          className="px-4 py-2 bg-massclaw-accent text-white rounded-lg text-sm hover:bg-massclaw-accent-light transition disabled:opacity-50"
        >
          {searching ? "Searching..." : "Search"}
        </button>
      </form>

      <div className="space-y-3">
        {results.map((r, i) => (
          <div key={i} className="bg-massclaw-surface border border-massclaw-border rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs px-2 py-0.5 rounded bg-massclaw-border text-massclaw-text-muted">
                {r.memory?.memory_type}
              </span>
              <div className="text-xs text-massclaw-text-muted">
                Similarity: {(r.similarity * 100).toFixed(1)}% &middot; Relevance: {(r.relevance_score * 100).toFixed(1)}%
              </div>
            </div>
            <p className="text-sm whitespace-pre-wrap">{r.memory?.content}</p>
          </div>
        ))}
        {results.length === 0 && query && !searching && (
          <p className="text-massclaw-text-muted text-center py-8">No results found</p>
        )}
      </div>
    </div>
  );
}
