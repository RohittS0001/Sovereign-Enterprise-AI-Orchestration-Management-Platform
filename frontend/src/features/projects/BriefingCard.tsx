import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../shared/api/client";
import { useAuth } from "../../shared/auth/AuthContext";
import { Card, ErrorNote, Spinner } from "../../shared/ui/components";

interface PortfolioBrief {
  as_of: string;
  projects: { key: string; name: string; risk_score: number; decision_needed: string | null }[];
  decisions: string[];
  spoken_text: string;
}

/** Chief-of-staff brief: decisions needed this week, a spoken version, drafted escalations. */
export function BriefingCard() {
  const { can } = useAuth();
  const [speaking, setSpeaking] = useState(false);
  const brief = useQuery({ queryKey: ["briefing"], queryFn: () => api<PortfolioBrief>("/briefing") });
  const drafts = useMutation({
    mutationFn: () => api<{ project: string; status: string; reason?: string }[]>("/briefing/escalations", { body: {} }),
  });
  const voice = typeof window !== "undefined" && "speechSynthesis" in window;

  const play = () => {
    if (!brief.data || !voice) return;
    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }
    const utterance = new SpeechSynthesisUtterance(brief.data.spoken_text);
    utterance.rate = 1.0;
    utterance.onend = () => setSpeaking(false);
    window.speechSynthesis.speak(utterance);
    setSpeaking(true);
  };

  return (
    <Card
      title="This week's brief"
      actions={
        <div className="row">
          {voice && (
            <button className="button secondary" onClick={play} disabled={!brief.data}>
              {speaking ? "Stop" : "Play briefing"}
            </button>
          )}
          {can("agent:query") && (
            <button className="button" onClick={() => drafts.mutate()} disabled={drafts.isPending || !brief.data?.decisions.length}>
              Draft escalations
            </button>
          )}
        </div>
      }
    >
      {brief.isLoading && <Spinner label="Preparing the brief…" />}
      <ErrorNote error={brief.error ?? drafts.error} />
      {brief.data && brief.data.decisions.length === 0 && <p className="small muted">No decisions needed this week.</p>}
      <ul className="small">
        {brief.data?.decisions.map((d) => (
          <li key={d}>{d}</li>
        ))}
      </ul>
      {drafts.data && (
        <div className="notice small" role="status">
          {drafts.data
            .map((d) => (d.status === "pending_approval" ? `${d.project}: draft waiting for approval` : `${d.project}: ${d.reason ?? d.status}`))
            .join(" · ")}
        </div>
      )}
    </Card>
  );
}
