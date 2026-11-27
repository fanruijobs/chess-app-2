import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { getTrainingSets } from "../api/client";
import type { Paginated, TrainingSet } from "../api/types";
import { Pagination } from "../components/Pagination";

const PAGE_SIZE = 50;

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function TrainingSetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedPage = Number(searchParams.get("page") ?? "1");
  const page = Number.isFinite(requestedPage) && requestedPage > 0
    ? Math.floor(requestedPage)
    : 1;
  const [data, setData] = useState<Paginated<TrainingSet> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getTrainingSets(page)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load sets.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page]);

  function changePage(nextPage: number) {
    if (nextPage === 1) setSearchParams({});
    else setSearchParams({ page: String(nextPage) });
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.count / PAGE_SIZE)) : 1;

  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>Training sets</h1>
          <p className="hint">
            This is the React slice. The Django training session, books, and
            analysis pages are still available from the navigation.
          </p>
        </div>
        <a className="button" href="/train-sets/">
          Open legacy page
        </a>
      </div>

      {loading && !data && <p className="loading">Loading sets…</p>}
      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th className="num">Total</th>
                <th className="num">Pending</th>
                <th className="num">Re-work</th>
                <th className="num">Passed</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((trainingSet) => (
                <tr key={trainingSet.id}>
                  <td>
                    <Link to={`/sets/${trainingSet.id}`}>
                      {trainingSet.name}
                    </Link>
                  </td>
                  <td className="num">{trainingSet.total}</td>
                  <td className="num">{trainingSet.pending}</td>
                  <td className="num">{trainingSet.rework}</td>
                  <td className="num">{trainingSet.passed}</td>
                  <td>{formatDate(trainingSet.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.results.length === 0 && (
            <p className="hint">No sets yet — create one in the legacy page.</p>
          )}
          <Pagination
            page={page}
            totalPages={totalPages}
            onChange={changePage}
          />
        </>
      )}
    </section>
  );
}
