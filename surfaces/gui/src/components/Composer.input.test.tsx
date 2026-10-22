import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

afterEach(() => cleanup());

describe("Composer draft input", () => {
  it("keeps typed text and spaces in the visible chat input", () => {
    render(<Composer {...props()} />);
    const input = screen.getByTestId("chat-input") as HTMLTextAreaElement;

    fireEvent.input(input, { target: { value: "xin chào Astra" } });

    expect(input.value).toBe("xin chào Astra");
    expect(input.classList.contains("block")).toBe(true);
  });

  it("does not submit an IME composition when Enter commits it", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    const input = screen.getByTestId("chat-input") as HTMLTextAreaElement;

    fireEvent.change(input, { target: { value: "chào" } });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229, which: 229 });

    expect(onSend).not.toHaveBeenCalled();
    expect(input.value).toBe("chào");

    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("chào", [], undefined);
  });

  it("buffers IME composition without overwriting draft and commits on compositionEnd", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    const input = screen.getByTestId("chat-input") as HTMLTextAreaElement;

    fireEvent.compositionStart(input);
    fireEvent.input(input, { target: { value: "b" } });
    fireEvent.input(input, { target: { value: "ba" } });
    fireEvent.input(input, { target: { value: "ban" } });
    fireEvent.compositionEnd(input, { target: { value: "bạn" } });

    expect(input.value).toBe("bạn");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("bạn", [], undefined);
  });
});
