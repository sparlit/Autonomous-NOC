'use client'

import { useState } from 'react';
import { Input } from "@/components/ui/input";
import { Search } from "lucide-react";

export function KnowledgeSearch() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'nanoc-secret-key';

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query) return;

    try {
        const res = await fetch(`${API_URL}/api/data/search?query=${encodeURIComponent(query)}`, {
          headers: { 'X-API-Key': API_KEY }
        });
        if (res.ok) setResults(await res.json());
    } catch (e) {
        console.error("Search failed", e);
    }
  };

  return (
    <div className="relative w-full max-w-sm">
      <form onSubmit={handleSearch}>
        <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-zinc-500" />
            <Input
                type="search"
                placeholder="Search knowledge base..."
                className="pl-8 bg-zinc-950 border-zinc-800"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
            />
        </div>
      </form>
      {results.length > 0 && (
        <div className="absolute top-full left-0 right-0 z-50 mt-2 p-2 bg-zinc-900 border border-zinc-800 rounded-md shadow-lg max-h-60 overflow-auto">
            {results.map((r, i) => (
                <div key={i} className="p-2 hover:bg-zinc-800 rounded text-xs border-b border-zinc-800 last:border-0">
                    <div className="font-bold text-zinc-300">{r.key}</div>
                    <div className="text-zinc-500 truncate">{r.value}</div>
                </div>
            ))}
            <button
                className="w-full p-1 text-[10px] text-zinc-500 hover:text-zinc-300 text-center"
                onClick={() => setResults([])}
            >
                Clear Results
            </button>
        </div>
      )}
    </div>
  );
}
