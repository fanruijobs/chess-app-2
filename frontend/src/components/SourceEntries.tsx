import type {
  SourceEntry,
  SourceType,
  TrainingOptions,
} from "../api/types";

export interface SourceEntryRow {
  key: string;
  sourceType: SourceType;
  bookSlug: string;
  pagesFrom: string;
  pagesTo: string;
  numbersFrom: string;
  numbersTo: string;
  player: string;
  dateFrom: string;
  dateTo: string;
  classification: string;
  fromSet: string;
}

let nextKey = 1;

export function newSourceEntry(
  options?: TrainingOptions,
): SourceEntryRow {
  return {
    key: `source-${nextKey++}`,
    sourceType: "book",
    bookSlug: options?.books[0]?.slug ?? "",
    pagesFrom: "",
    pagesTo: "",
    numbersFrom: "",
    numbersTo: "",
    player: options?.players[0] ?? "",
    dateFrom: "",
    dateTo: "",
    classification: "",
    fromSet: options?.sets[0] ? String(options.sets[0].id) : "",
  };
}

export function sourceRowsToEntries(rows: SourceEntryRow[]): SourceEntry[] {
  return rows.map((row) => {
    if (row.sourceType === "book") {
      return {
        source_type: "book",
        book_slug: row.bookSlug,
        pages_from: row.pagesFrom,
        pages_to: row.pagesTo,
        numbers_from: row.numbersFrom,
        numbers_to: row.numbersTo,
      };
    }
    if (row.sourceType === "turning_points") {
      return {
        source_type: "turning_points",
        player: row.player,
        date_from: row.dateFrom,
        date_to: row.dateTo,
        classifications: row.classification ? [row.classification] : [],
      };
    }
    return {
      source_type: "rework",
      from_set: row.fromSet,
    };
  });
}

interface SourceEntriesProps {
  options: TrainingOptions;
  value: SourceEntryRow[];
  onChange: (rows: SourceEntryRow[]) => void;
  excludeSetId?: number;
}

export function SourceEntries({
  options,
  value,
  onChange,
  excludeSetId,
}: SourceEntriesProps) {
  const setOptions = options.sets.filter(
    (trainingSet) => trainingSet.id !== excludeSetId,
  );

  function update(index: number, patch: Partial<SourceEntryRow>) {
    onChange(
      value.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row,
      ),
    );
  }

  function remove(index: number) {
    onChange(value.filter((_, rowIndex) => rowIndex !== index));
  }

  return (
    <div>
      {value.map((row, index) => (
        <div className="source-entry" key={row.key}>
          <div className="source-fields">
            <label>
              Source
              <select
                value={row.sourceType}
                onChange={(event) =>
                  update(index, {
                    sourceType: event.target.value as SourceType,
                  })
                }
              >
                <option value="book">Book</option>
                <option value="turning_points">Game turning points</option>
                <option value="rework">Re-work positions</option>
              </select>
            </label>

            {row.sourceType === "book" && (
              <>
                <label>
                  Book
                  <select
                    value={row.bookSlug}
                    onChange={(event) =>
                      update(index, { bookSlug: event.target.value })
                    }
                  >
                    {options.books.map((book) => (
                      <option key={book.slug} value={book.slug}>
                        {book.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Pages
                  <span className="inline-inputs">
                    <input
                      type="number"
                      min="1"
                      placeholder="from"
                      value={row.pagesFrom}
                      onChange={(event) =>
                        update(index, { pagesFrom: event.target.value })
                      }
                    />
                    <span>–</span>
                    <input
                      type="number"
                      min="1"
                      placeholder="to"
                      value={row.pagesTo}
                      onChange={(event) =>
                        update(index, { pagesTo: event.target.value })
                      }
                    />
                  </span>
                </label>
                <label>
                  Puzzle numbers
                  <span className="inline-inputs">
                    <input
                      type="text"
                      placeholder="from"
                      value={row.numbersFrom}
                      onChange={(event) =>
                        update(index, { numbersFrom: event.target.value })
                      }
                    />
                    <span>–</span>
                    <input
                      type="text"
                      placeholder="to"
                      value={row.numbersTo}
                      onChange={(event) =>
                        update(index, { numbersTo: event.target.value })
                      }
                    />
                  </span>
                </label>
              </>
            )}

            {row.sourceType === "turning_points" && (
              <>
                <label>
                  Player
                  <select
                    value={row.player}
                    onChange={(event) =>
                      update(index, { player: event.target.value })
                    }
                  >
                    {options.players.map((player) => (
                      <option key={player} value={player}>
                        {player}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Games
                  <span className="inline-inputs">
                    <input
                      type="date"
                      value={row.dateFrom}
                      onChange={(event) =>
                        update(index, { dateFrom: event.target.value })
                      }
                    />
                    <span>to</span>
                    <input
                      type="date"
                      value={row.dateTo}
                      onChange={(event) =>
                        update(index, { dateTo: event.target.value })
                      }
                    />
                  </span>
                </label>
                <label>
                  Classification
                  <select
                    value={row.classification}
                    onChange={(event) =>
                      update(index, { classification: event.target.value })
                    }
                  >
                    <option value="">(any)</option>
                    {options.classifications.map((classification) => (
                      <option
                        key={classification.value}
                        value={classification.value}
                      >
                        {classification.label}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}

            {row.sourceType === "rework" && (
              <label>
                From set
                <select
                  value={row.fromSet}
                  onChange={(event) =>
                    update(index, { fromSet: event.target.value })
                  }
                >
                  <option value="">— choose a set —</option>
                  {setOptions.map((trainingSet) => (
                    <option
                      key={trainingSet.id}
                      value={String(trainingSet.id)}
                    >
                      {trainingSet.name} ({trainingSet.rework} re-work)
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <button
            type="button"
            className="remove-source"
            title="Remove this source"
            onClick={() => remove(index)}
          >
            ✕
          </button>
        </div>
      ))}
      <p className="row-form">
        <button
          type="button"
          className="button"
          onClick={() => onChange([...value, newSourceEntry(options)])}
        >
          + Add another source
        </button>
      </p>
    </div>
  );
}
