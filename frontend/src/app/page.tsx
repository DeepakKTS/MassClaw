"use client";

import { Suspense } from "react";
import { MissionInput } from "@/components/mission/MissionInput";
import { MissionDeck } from "@/components/mission/MissionDeck";

export default function MissionControlPage() {
  return (
    <div className="max-w-5xl mx-auto flex flex-col h-[calc(100vh-11rem)] py-2">
      {/* Timeline */}
      <div className="flex-1 overflow-hidden min-h-0">
        <Suspense fallback={<div className="h-40 animate-pulse" />}>
          <MissionDeck />
        </Suspense>
      </div>

      {/* Mission Input — above dock with gap */}
      <div className="shrink-0 flex justify-center pt-4 pb-2">
        <MissionInput />
      </div>
    </div>
  );
}
