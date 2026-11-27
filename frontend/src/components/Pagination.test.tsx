import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("renders the current page and advances", async () => {
    const onChange = vi.fn();
    render(<Pagination page={1} totalPages={2} onChange={onChange} />);

    expect(screen.getByText("page 1 of 2")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /previous/i }),
    ).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(onChange).toHaveBeenCalledWith(2);
  });

  it("renders nothing for a single page", () => {
    const { container } = render(
      <Pagination page={1} totalPages={1} onChange={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
