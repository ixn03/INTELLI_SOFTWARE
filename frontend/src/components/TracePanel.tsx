"use client";

import axios from "axios";
import { useState } from "react";

interface Props {
  projectId: string;
}

export default function TracePanel({
  projectId,
}: Props) {

  const [tag, setTag] = useState("");
  const [trace, setTrace] = useState<any>(null);

  async function runTrace() {

    if (!tag) return;

    const res = await axios.get(
      `http://127.0.0.1:8000/projects/${projectId}/trace/${tag}`
    );

    setTrace(res.data);
  }

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 h-full">

      <h2 className="text-2xl font-bold mb-6">
        Deterministic Trace
      </h2>

      <div className="flex gap-3 mb-6">

        <input
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          placeholder="Enter tag..."
          className="bg-zinc-800 border border-zinc-700 rounded-xl px-4 py-3 flex-1"
        />

        <button
          onClick={runTrace}
          className="bg-blue-600 hover:bg-blue-500 px-5 rounded-xl"
        >
          Trace
        </button>
      </div>

      {trace && (
        <div>

          <div className="mb-6">

            <div className="text-zinc-400 text-sm">
              Summary
            </div>

            <div className="text-lg">
              {trace.summary}
            </div>
          </div>

          <div className="flex flex-col gap-3">

            {trace.causes.map((cause: any, index: number) => (
              <div
                key={index}
                className="bg-zinc-800 rounded-xl p-4"
              >

                <div className="font-semibold text-blue-400 mb-2">
                  {cause.tag}
                </div>

                <div className="text-sm text-zinc-300">
                  {cause.instruction_type}
                </div>

                <div className="text-xs text-zinc-500 mt-2">
                  {cause.raw_text}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
