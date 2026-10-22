import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { WindowResizeHandles } from "./WindowResizeHandles";
import * as tauri from "../tauri";

afterEach(() => cleanup());

describe("WindowResizeHandles", () => {
  it("renders corners and edges with proper resize cursors and triggers resize dragging", () => {
    const spy = vi.spyOn(tauri, "windowStartResizeDragging").mockResolvedValue(true);
    const { container } = render(<WindowResizeHandles />);

    const nw = container.querySelector('[data-direction="NorthWest"]');
    expect(nw).toBeTruthy();
    expect(nw?.classList.contains("cursor-nwse-resize")).toBe(true);
    fireEvent.mouseDown(nw!, { button: 0 });
    expect(spy).toHaveBeenCalledWith("NorthWest");

    const ne = container.querySelector('[data-direction="NorthEast"]');
    expect(ne).toBeTruthy();
    expect(ne?.classList.contains("cursor-nesw-resize")).toBe(true);
    fireEvent.mouseDown(ne!, { button: 0 });
    expect(spy).toHaveBeenCalledWith("NorthEast");

    const north = container.querySelector('[data-direction="North"]');
    expect(north).toBeTruthy();
    expect(north?.classList.contains("cursor-ns-resize")).toBe(true);
    fireEvent.mouseDown(north!, { button: 0 });
    expect(spy).toHaveBeenCalledWith("North");

    const east = container.querySelector('[data-direction="East"]');
    expect(east).toBeTruthy();
    expect(east?.classList.contains("cursor-ew-resize")).toBe(true);
    fireEvent.mouseDown(east!, { button: 0 });
    expect(spy).toHaveBeenCalledWith("East");
  });
});
