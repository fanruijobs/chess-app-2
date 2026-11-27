import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getTrainingSet, getTrainingSetItems } from "../api/client";
import type { Paginated, TrainingSet, TrainingSetItem } from "../api/types";
import { Pagination } from "../components/Pagination";

const PAGE_SIZE = 50;

function statusLabel(status: string): string {
  if (status === "rework") return "Re-work";
  if (status === "passed") return "Passed";
  return "Pending";
}

export function TrainingSetDetailPage() {
  const { id } = useParams<{ id: string }>();
  const setId = Number(id);
  const [trainingSet, setTrainingSet] = useState<TrainingSet | null>(null);
  const [items, setItems] = useState<Paginated<TrainingSetItem> | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!Number.isFinite(setId) || setId <= 0) {
      setError("Invalid set.");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([getTrainingSet(setId), getTrainingSetItems(setId, page)])
      .then(([setResult, itemResult]) => {
        if (cancelled) return;
        setTrainingSet(setResult);
        setItems(itemResult);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load set.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [setId, page]);

  const totalPages = items ? Math.max(1, Math.ceil(items.count / PAGE_SIZE)) : 1;

  if (loading && !trainingSet) return <p className="loading">Loading set…</p>;
  if (error) return <p className="error">{error}</p>;
  if (!trainingSet) return null;

  return (
    <section>
      <p className="breadcrumb">
        <Link to="/">Training sets</Link> / {trainingSet.name}
      </p>
      <div className="page-heading">
        <div>
          <h1>{trainingSet.name}</h1>
          <p className="hint">
            {trainingSet.owner} · created{" "}
            {new Date(trainingSet.created_at).toLocaleString()}
          </p>
        </div>
        <a className="button" href={`/train-sets/${trainingSet.id}/`}>
          Open legacy set page
        </a>
      </div>

      <div className="stats-grid">
        <div className="stat">
          <strong>{trainingSet.total}</strong>
          <span>positions</span>
        </div>
        <div className="stat">
          <strong>{trainingSet.pending}</strong>
          <span>pending</span>
        </div>
        <div className="stat">
          <strong>{trainingSet.rework}</strong>
          <span>re-work</span>
        </div>
        <div className="stat">
          <strong>{trainingSet.passed}</strong>
          <span>passed</span>
        </div>
        <div className="stat">
          <strong>
            {trainingSet.pass_rate === null
              ? "—"
              : `${trainingSet.pass_rate}%`}
          </strong>
          <span>pass rate</span>
        </div>
      </div>

      <h2>Positions</h2>
      {items && items.results.length === 0 && (
        <p className="hint">No positions in this set.</p>
      )}
      {items && items.results.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th>Label</th>
                <th>Status</th>
                <th className="num">Attempts</th>
                <th>Training</th>
              </tr>
            </thead>
            <tbody>
              {items.results.map((item) => (
                <tr key={item.id}>
                  <td>{item.label || `#${item.id}`}</td>
                  <td>
                    <span className={`badge badge-${item.status}`}>
                      {statusLabel(item.status)}
                    </span>
                  </td>
                  <td className="num">{item.attempts}</td>
                  <td>
                    <a href={`/train-sets/${trainingSet.id}/items/${item.id}/`}>
                      Train
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination page={page} totalPages={totalPages} onChange={setPage} />
        </>
      )}
    </section>
  );
}
