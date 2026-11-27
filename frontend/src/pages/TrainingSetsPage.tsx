import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import {
  cloneTrainingSet,
  createTrainingSet,
  getTrainingOptions,
  getTrainingSets,
} from "../api/client";
import type {
  Paginated,
  TrainingOptions,
  TrainingSet,
} from "../api/types";
import { Pagination } from "../components/Pagination";
import {
  SourceEntries,
  newSourceEntry,
  sourceRowsToEntries,
  type SourceEntryRow,
} from "../components/SourceEntries";

const PAGE_SIZE = 50;

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function TrainingSetsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedPage = Number(searchParams.get("page") ?? "1");
  const page = Number.isFinite(requestedPage) && requestedPage > 0
    ? Math.floor(requestedPage)
    : 1;
  const [data, setData] = useState<Paginated<TrainingSet> | null>(null);
  const [options, setOptions] = useState<TrainingOptions | null>(null);
  const [entries, setEntries] = useState<SourceEntryRow[]>([]);
  const [name, setName] = useState("");
  const [cloneSource, setCloneSource] = useState("");
  const [cloneName, setCloneName] = useState("");
  const [error, setError] = useState("");
  const [createError, setCreateError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getTrainingOptions()
      .then((result) => {
        if (cancelled) return;
        setOptions(result);
        setEntries([newSourceEntry(result)]);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setCreateError(err instanceof Error ? err.message : "Could not load options.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError("");
    setBusy(true);
    try {
      const trainingSet = await createTrainingSet(
        name,
        sourceRowsToEntries(entries),
      );
      navigate(`/sets/${trainingSet.id}`);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Could not create set.");
    } finally {
      setBusy(false);
    }
  }

  async function handleClone(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!cloneSource) return;
    setCreateError("");
    setBusy(true);
    try {
      const trainingSet = await cloneTrainingSet(
        Number(cloneSource),
        cloneName,
      );
      navigate(`/sets/${trainingSet.id}`);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Could not clone set.");
    } finally {
      setBusy(false);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.count / PAGE_SIZE)) : 1;

  return (
    <section>
      <div className="page-heading">
        <div>
          <h1>Training sets</h1>
          <p className="hint">
            Create and manage sets here; training positions open in the new
            session board.
          </p>
        </div>
        <a className="button" href="/train-sets/">
          Open legacy page
        </a>
      </div>

      {options && (
        <div className="card form-card">
          <h2>Create a set</h2>
          {createError && <p className="error">{createError}</p>}
          <form onSubmit={handleCreate}>
            <label>
              Name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Steps p003-p007 + my blunders"
                required
              />
            </label>
            <SourceEntries
              options={options}
              value={entries}
              onChange={setEntries}
            />
            <button type="submit" disabled={busy}>
              {busy ? "Creating…" : "Create set"}
            </button>
          </form>

          <h2>Clone a set</h2>
          <form className="inline-form" onSubmit={handleClone}>
            <select
              value={cloneSource}
              onChange={(event) => setCloneSource(event.target.value)}
              required
            >
              <option value="">— choose a set —</option>
              {options.sets.map((trainingSet) => (
                <option key={trainingSet.id} value={String(trainingSet.id)}>
                  {trainingSet.name}
                </option>
              ))}
            </select>
            <input
              value={cloneName}
              onChange={(event) => setCloneName(event.target.value)}
              placeholder="new name (optional)"
            />
            <button type="submit" disabled={busy || !cloneSource}>
              Clone set
            </button>
          </form>
        </div>
      )}

      {loading && !data && <p className="loading">Loading sets…</p>}
      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <h2>Your sets</h2>
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
            <p className="hint">No sets yet — create one above.</p>
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
