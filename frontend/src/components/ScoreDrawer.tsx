import { useEffect, useState } from "react";
import { fetchScore } from "../api";
import type { ScoreBreakdown } from "../types";
import { SOURCE_LABELS } from "../constants";

/**
 * The scoring model's demo surface in the UI: the same per-source breakdown the
 * `roam explain` CLI prints. The point is that a ranking is defensible line by
 * line rather than being an opaque number.
 */
export default function ScoreDrawer({
  placeId, onClose,
}: { placeId: string; onClose: () => void }) {
  const [data, setData] = useState<ScoreBreakdown | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    fetchScore(placeId)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [placeId]);

  const b = data?.breakdown;
  const totalWeight = b?.signals.reduce((sum, s) => sum + s.weight, 0) ?? 0;

  return (
    <div className="drawer">
      <button className="close" onClick={onClose} aria-label="Close">
        ×
      </button>
      {error && <div className="banner err">Could not load score: {error}</div>}
      {!data && !error && <div className="muted">Loading…</div>}

      {data && (
        <>
          <h2>{data.name}</h2>
          <div className="muted" style={{ marginBottom: 14 }}>
            Composite score{" "}
            <strong style={{ color: "var(--accent)", fontSize: 17 }}>
              {data.composite_score?.toFixed(1) ?? "—"}
            </strong>{" "}
            / 100
          </div>

          {!b && <div className="muted">No breakdown stored. Run `roam score`.</div>}

          {b && (
            <>
              <div className="section-title">Per-source contribution</div>
              {b.signals.map((s) => (
                <div className="sig" key={s.source}>
                  <div className="hd">
                    <span className="src">{SOURCE_LABELS[s.source] ?? s.source}</span>
                    <span className="muted mono">
                      quality {s.quality.toFixed(3)} · weight {s.weight.toFixed(3)}
                    </span>
                    <span className="muted mono" style={{ marginLeft: "auto" }}>
                      {totalWeight > 0 ? Math.round((s.weight / totalWeight) * 100) : 0}%
                    </span>
                  </div>
                  <div className="bar">
                    <i style={{ width: `${s.quality * 100}%` }} />
                  </div>
                  <div className="muted mono" style={{ fontSize: 11, marginTop: 6 }}>
                    credibility {s.weight_components.credibility.toFixed(2)} × volume{" "}
                    {s.weight_components.volume.toFixed(2)} × recency{" "}
                    {s.weight_components.recency.toFixed(2)}
                    {s.age_days != null ? ` · ${Math.round(s.age_days)}d old` : ""}
                  </div>
                  {s.notes.map((note, i) => (
                    <p className="note" key={i}>
                      {note}
                    </p>
                  ))}
                </div>
              ))}

              <div className="section-title" style={{ marginTop: 16 }}>
                How it combines
              </div>
              <div className="math">
                <div>
                  <span>weighted mean quality</span>
                  <span>{b.raw_base.toFixed(4)}</span>
                </div>
                <div>
                  <span>total evidence (Σ weights)</span>
                  <span>{b.evidence.toFixed(4)}</span>
                </div>
                <div>
                  <span>evidence confidence</span>
                  <span>{b.evidence_confidence.toFixed(4)}</span>
                </div>
                <div>
                  <span>shrunk toward neutral 0.50</span>
                  <span>{b.base.toFixed(4)}</span>
                </div>
                <div>
                  <span>cross-source agreement</span>
                  <span>{b.agreement.toFixed(4)}</span>
                </div>
                <div>
                  <span>corroboration bonus ({b.source_count} sources)</span>
                  <span>+{b.corroboration_bonus.toFixed(4)}</span>
                </div>
                <div>
                  <span>composite × 100</span>
                  <span>{b.composite_score.toFixed(1)}</span>
                </div>
              </div>
              <div className="muted" style={{ marginTop: 10, fontSize: 11 }}>
                Ratings are z-scored against each source's own measured
                distribution before combining — a 4.3 is below Google's Madrid
                mean and above Yelp's, so they are not the same claim.
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
