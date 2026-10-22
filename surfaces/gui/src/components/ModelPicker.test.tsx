import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ModelPicker, MODEL_FAVORITES_KEY } from "./ModelPicker";

const OPTIONS = Array.from({ length: 10 }, (_, index) => ({
  value: `provider:model-${index + 1}`,
  label: `Model ${index + 1}`,
}));

afterEach(cleanup);

beforeEach(() => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    clear: () => values.clear(),
    key: (index: number) => Array.from(values.keys())[index] ?? null,
    get length() {
      return values.size;
    },
  };
  Object.defineProperty(window, "localStorage", { value: storage, configurable: true });
});

describe("ModelPicker", () => {
  it("keeps a long model list in a compact scroll region", () => {
    render(<ModelPicker value={OPTIONS[0].value} options={OPTIONS} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose model" }));

    expect(screen.getByPlaceholderText("Search models")).toBeTruthy();
    expect(document.querySelector(".model-picker-list")?.getAttribute("style")).toContain(
      "max-height: 320px",
    );
    expect(screen.getAllByRole("option")).toHaveLength(10);
  });

  it("filters every model through the search field", () => {
    render(<ModelPicker value={OPTIONS[0].value} options={OPTIONS} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose model" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Search models" }), {
      target: { value: "model-10" },
    });

    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByText("Model 10")).toBeTruthy();
  });

  it("pins a model to Favorites and persists the choice locally", () => {
    render(<ModelPicker value={OPTIONS[0].value} options={OPTIONS} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose model" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Add to favorites" })[2]);

    expect(screen.getByText("Favorites")).toBeTruthy();
    expect(screen.getAllByRole("option")[0].textContent).toContain("Model 3");
    expect(JSON.parse(window.localStorage.getItem(MODEL_FAVORITES_KEY) || "[]")).toEqual([
      "provider:model-3",
    ]);
    expect(screen.getByRole("button", { name: "Remove from favorites" })).toBeTruthy();
  });
});
