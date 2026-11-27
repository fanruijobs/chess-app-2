import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  SourceEntries,
  newSourceEntry,
  sourceRowsToEntries,
} from "./SourceEntries";
import type { TrainingOptions } from "../api/types";

const options: TrainingOptions = {
  books: [{ slug: "polgar-5334", title: "Polgar 5334" }],
  players: ["alice"],
  classifications: [{ value: "hung_piece", label: "Hung a piece" }],
  sets: [{ id: 1, name: "Set A", rework: 2 }],
  rework_sets: [{ id: 1, name: "Set A", rework: 2 }],
  rework_books: [{ slug: "polgar-5334", title: "Polgar 5334" }],
};

describe("SourceEntries", () => {
  it("converts book rows to API entries", () => {
    const row = newSourceEntry(options);
    row.pagesFrom = "16";
    row.pagesTo = "18";
    expect(sourceRowsToEntries([row])).toEqual([
      {
        source_type: "book",
        book_slug: "polgar-5334",
        pages_from: "16",
        pages_to: "18",
        numbers_from: "",
        numbers_to: "",
      },
    ]);
  });

  it("adds and removes source rows", async () => {
    const onChange = vi.fn();
    const row = newSourceEntry(options);
    render(
      <SourceEntries
        options={options}
        value={[row]}
        onChange={onChange}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: /add another source/i }),
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toHaveLength(2);

    await userEvent.click(
      screen.getByTitle("Remove this source"),
    );
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange.mock.calls[1][0]).toHaveLength(0);
  });
});
