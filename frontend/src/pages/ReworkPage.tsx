import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getReworkItems, getTrainingOptions } from "../api/client";
import type {
  Paginated,
  ReworkItem,
  TrainingOptions,
} from "../api/types";
import { Pagination } from "../components/Pagination";

const PAGE_SIZE = 50;

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function ReworkPage() {
  const [options, setOptions] = useState<TrainingOptions | null>(null);
  const [data, setData] = useState<Paginated<ReworkItem> | null>(null);
  const [setFilter, setSetFilter] = useState("");
  const [bookFilter, setBookFilter] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getTrainingOptions()
      .then(setOptions)
      .catch(() => {
        /* filters are optional */
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getReworkItems(page, setFilter, bookFilter)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load re-work.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, setFilter, bookFilter]);

  const totalPages = data ? Math.max(1, Math.ceil(data.count / PAGE_SIZE)) : 1;

  return (
    <section>
      <p className="breadcrumb">
        <Link to="/">Training sets</Link> / Re-work
      </p>
      <div className="page-heading">
        <div>
          <h1>Re-work</h1>
          <p className="hint">
            Every re-work position across your sets. Open one to train it in
            the session board.
          </p>
        </div>
      </div>

      {options && (
        <p className="filter-row">
          <label>
            Training set
            <select
              value={setFilter}
              onChange={(event) => {
                setPage(1);
                setSetFilter(event.target.value);
              }}
            >
              <option value="">— all sets —</option>
              {options.rework_sets.map((trainingSet) => (
                <option key={trainingSet.id} value={String(trainingSet.id)}>
                  {trainingSet.name} ({trainingSet.rework})
                </option>
              ))}
            </select>
          </label>
          <label>
            Book
            <select
              value={bookFilter}
              onChange={(event) => {
                setPage(1);
                setBookFilter(event.target.value);
              }}
            >
              <option value="">— all books —</option>
              {options.rework_books.map((book) => (
                <option key={book.slug} value={book.slug}>
                  {book.title}
                </option>
              ))}
            </select>
          </label>
        </p>
      )}

      {loading && !data && <p className="loading">Loading re-work…</p>}
      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Set</th>
                <th>Position</th>
                <th>Book</th>
                <th>Best move</th>
                <th className="num">Attempts</th>
                <th>Last trained</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((item, index) => (
                <tr key={`${item.set_id}-${item.id}`}>
                  <td className="num">
                    {(page - 1) * PAGE_SIZE + index + 1}
                  </td>
                  <td>
                    <Link to={`/sets/${item.set_id}`}>{item.set_name}</Link>
                  </td>
                  <td>
                    <Link
                      to={`/sets/${item.set_id}/items/${item.id}/?status=rework`}
                    >
                      {item.label || `#${item.id}`}
                    </Link>
                  </td>
                  <td>{item.book_title ?? "—"}</td>
                  <td>{item.san_best || "—"}</td>
                  <td className="num">{item.attempts}</td>
                  <td>{formatDate(item.last_trained_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.results.length === 0 && (
            <p className="hint">No re-work positions yet.</p>
          )}
          <Pagination page={page} totalPages={totalPages} onChange={setPage} />
        </>
      )}
    </section>
  );
}
