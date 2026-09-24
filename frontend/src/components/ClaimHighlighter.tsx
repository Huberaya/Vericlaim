"use client";

import type { ReactNode } from "react";
import { violationSeverity, type ClaimEvaluation, type ViolationSeverity } from "@/lib/types";

type Props = {
  sourceText: string;
  evaluations: ClaimEvaluation[];
  onSelect: (evaluations: ClaimEvaluation[]) => void;
};

type HighlightTone = "critical" | "warning" | "info" | "positive";
type ClaimSpan = { start: number; end: number; item: ClaimEvaluation };

const toneWeight: Record<HighlightTone, number> = {
  critical: 4,
  warning: 3,
  info: 2,
  positive: 1,
};

function toneFor(item: ClaimEvaluation): HighlightTone {
  const severity: ViolationSeverity = violationSeverity(item);
  if (severity === "CRITICAL") return "critical";
  if (severity === "WARNING") return "warning";
  return item.verdict === "COMPLIANT" || item.safe_harbor_applicable ? "positive" : "info";
}

export default function ClaimHighlighter({ sourceText, evaluations, onSelect }: Props) {
  if (!sourceText) {
    return <div className="claim-highlight-box claim-highlight-empty">Aucun texte source à afficher.</div>;
  }

  const boundaries = new Set<number>([0, sourceText.length]);
  const spans: ClaimSpan[] = evaluations
    .filter((item) => item.end_offset > item.start_offset && item.start_offset < sourceText.length)
    .map((item) => {
      const start = Math.max(0, Math.min(sourceText.length, item.start_offset));
      const end = Math.max(start, Math.min(sourceText.length, item.end_offset));
      boundaries.add(start);
      boundaries.add(end);
      return { start, end, item };
    });

  const sortedBoundaries = [...boundaries].sort((a, b) => a - b);
  const parts: ReactNode[] = [];
  for (let index = 0; index < sortedBoundaries.length - 1; index += 1) {
    const start = sortedBoundaries[index];
    const end = sortedBoundaries[index + 1];
    if (end <= start) continue;
    const active = spans.filter((span) => span.start <= start && span.end >= end);
    const segment = sourceText.slice(start, end);
    if (active.length === 0) {
      parts.push(<span key={`plain-${start}`}>{segment}</span>);
      continue;
    }

    const assessments = [...new Map(active.map((span) => [`${span.item.claim_id}:${span.item.rule_id}`, span.item])).values()];
    const chosen = assessments
      .map((item) => ({ item, tone: toneFor(item) }))
      .reduce((current, next) => toneWeight[next.tone] > toneWeight[current.tone] ? next : current);
    const rules = [...new Set(assessments.map((item) => item.rule_id))].join(", ");
    const claimText = [...new Set(assessments.map((item) => item.claim_text))].join(" · ");

    parts.push(
      <button
        key={`claim-${start}`}
        type="button"
        className={`claim-mark claim-mark-${chosen.tone}`}
        title={`${rules} — cliquer pour le détail`}
        aria-label={`Allégation : ${claimText}. Fondements : ${rules}. Ouvrir la fiche de remédiation.`}
        onClick={() => onSelect(assessments)}
      >
        {segment}
      </button>,
    );
  }

  return (
    <div className="claim-highlight-box" aria-label="Texte audité et allégations surlignées">
      {parts}
    </div>
  );
}
