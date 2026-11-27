import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import {
  addTrainingSetItems,
  getTrainingOptions,
  getTrainingSet,
  getTrainingSetItems,
  markAll,
} from "../api/client";
import type {
  Paginated,
  TrainingItemStatus,
  TrainingOptions,
  TrainingSet,
  TrainingSetItem,
} from "../api/types";
import { Pagination } from "../components/Pagination";
import {
  SourceEntries,
  newSourceEntry,
  sourceRowsToEntries,
  type SourceEntryRow,
} from "../components/SourceEntries";

const PAGE_SIZE = 50;
const CATEGORIES: { value: TrainingItemStatus; label: string }[] = [
  { value: "pending", label: "Pending" },
  { value: "rework", label: "Re-work" },
  { value: "passed", label: "Passed" },
];

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function statusLabel(status: string): string {
  if (status === "rework") return "Re-work";
  if (status === "passed") return "Passed";
  return "Pending";
}

export function TrainingSetDetailPage() {
  const { id } = useParams<{ id: string }>();
  const setId = Number(id);
  const [searchParams, setSearchParams] = useSearchParams();
  const rawCategory = searchParams.get("status") ?? "pending";
  const category: TrainingItemStatus = CATEGORIES.some(
    (item) => item.value === rawCategory,
  )
    ? (rawCategory as TrainingItemStatus)
    : "pending";
  const [trainingSet, setTrainingSet] = useState<TrainingSet | null>(null);
  const [items, setItems] = useState<Paginated<TrainingSetItem> | null>(null);
  const [options, setOptions] = useState<TrainingOptions | null>(null);
  const [entries, setEntries] = useState<SourceEntryRow[]>([]);
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
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
    Promise.all([
      getTrainingSet(setId),
      getTrainingSetItems(setId, page, category),
    ])
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
  }, [setId, page, category, refresh]);

  useEffect(() => {
    let cancelled = false;
    getTrainingOptions()
      .then((result) => {
        if (cancelled) return;
        setOptions(result);
        setEntries([newSourceEntry(result)]);
      })
      .catch(() => {
        /* the add-positions form is optional */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function changeCategory(nextCategory: TrainingItemStatus) {
    setPage(1);
    if (nextCategory === "pending") setSearchParams({});
    else setSearchParams({ status: nextCategory });
  }

  async function handleMarkAll(result: "passed" | "rework") {
    if (!trainingSet) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await markAll(trainingSet.id, result, category);
      setMessage(`Marked ${response.marked} position(s).`);
      setRefresh((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not mark category.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trainingSet) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await addTrainingSetItems(
        trainingSet.id,
        sourceRowsToEntries(entries),
      );
      setMessage(`Added ${response.added} position(s).`);
      setEntries([newSourceEntry(options ?? undefined)]);
      setRefresh((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add positions.");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !trainingSet) return <p className="loading">Loading set…</p>;
  if (error && !trainingSet) return <p className="error">{error}</p>;
  if (!trainingSet) return null;

  const totalPages = items ? Math.max(1, Math.ceil(items.count / PAGE_SIZE)) : 1;
  const firstItem = items?.results[0] ?? null;
  const categoryQuery =
    category === "pending" ? "" : `?status=${category}`;

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
        <a className="button" href={`/train-sets/${trainingSet.id}/pdf/`}>
          Export PDF
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

      <p className="filter-row">
        Categories:
        {CATEGORIES.map((item) => (
          <button
            key={item.value}
            type="button"
            className={category === item.value ? "button active" : "button"}
            onClick={() => changeCategory(item.value)}
          >
            {item.label}
          </button>
        ))}
      </p>

      <p className="row-form">
        {firstItem && (
          <Link
            className="button"
            to={`/sets/${trainingSet.id}/items/${firstItem.id}/${categoryQuery}`}
          >
            Start training
          </Link>
        )}
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => handleMarkAll("passed")}
        >
          Mark category passed
        </button>
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => handleMarkAll("rework")}
        >
          Mark category re-work
        </button>
      </p>

      {message && <p className="note">{message}</p>}
      {error && <p className="error">{error}</p>}

      {options && (
        <div className="card form-card">
          <h2>Add positions</h2>
          <form onSubmit={handleAdd}>
            <SourceEntries
              options={options}
              value={entries}
              onChange={setEntries}
              excludeSetId={trainingSet.id}
            />
            <button type="submit" disabled={busy}>
              Add to set
            </button>
          </form>
        </div>
      )}

      <h2>Positions</h2>
      {items && items.results.length === 0 && (
        <p className="hint">No positions in this category.</p>
      )}
      {items && items.results.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Position</th>
                <th>Best move</th>
                <th>Status</th>
                <th className="num">Attempts</th>
                <th>Last trained</th>
              </tr>
            </thead>
            <tbody>
              {items.results.map((item, index) => (
                <tr key={item.id}>
                  <td className="num">{page * PAGE_SIZE + index + 1}</td>
                  <td>
                    <Link
                      to={`/sets/${trainingSet.id}/items/${item.id}/${categoryQuery}`}
                    >
                      {item.label || `#${item.id}`}
                    </Link>
                  </td>
                  <td>{item.san_best || "—"}</td>
                  <td>
                    <span className={`badge badge-${item.status}`}>
                      {statusLabel(item.status)}
                    </span>
                  </td>
                  <td className="num">{item.attempts}</td>
                  <td>{formatDate(item.last_trained_at)}</td>
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
