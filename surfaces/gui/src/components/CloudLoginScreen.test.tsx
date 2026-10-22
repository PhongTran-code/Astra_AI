import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { CloudStatus } from "../api";
import { announceCloudChanged, cloudLogin, getCloudStatus, waitForCloudSignIn } from "../api";
import { CloudLoginScreen } from "./CloudLoginScreen";

vi.mock("../api", () => ({
  announceCloudChanged: vi.fn(),
  cloudLogin: vi.fn(),
  getCloudStatus: vi.fn(),
  waitForCloudSignIn: vi.fn(),
}));

const signedOut: CloudStatus = { signed_in: false, account: "", user_id: "" };

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CloudLoginScreen", () => {
  it("keeps OAuth in the browser and updates the screen after the callback", async () => {
    vi.mocked(getCloudStatus).mockResolvedValue(signedOut);
    vi.mocked(cloudLogin).mockResolvedValue({ ok: true });
    let finishSignIn: ((status: CloudStatus | null) => void) | undefined;
    vi.mocked(waitForCloudSignIn).mockImplementation((done) => {
      finishSignIn = done;
      return vi.fn();
    });
    const onClose = vi.fn();

    render(<CloudLoginScreen open onClose={onClose} />);
    fireEvent.click(screen.getByTestId("cloud-login-primary"));

    await waitFor(() => {
      expect(cloudLogin).toHaveBeenCalledOnce();
      expect(waitForCloudSignIn).toHaveBeenCalledOnce();
    });
    expect(screen.getByText("Finish signing in in your browser, then come back here.")).toBeTruthy();

    act(() => finishSignIn?.({ signed_in: true, account: "ada@example.com", user_id: "ada" }));

    expect(await screen.findByText("You're connected")).toBeTruthy();
    expect(screen.getByText("Signed in as ada@example.com.")).toBeTruthy();
    expect(announceCloudChanged).toHaveBeenCalledOnce();
  });

  it("shows a retryable failure when the browser flow cannot start", async () => {
    vi.mocked(getCloudStatus).mockResolvedValue(signedOut);
    vi.mocked(cloudLogin).mockRejectedValue(new Error("sidecar unavailable"));

    render(<CloudLoginScreen open onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId("cloud-login-primary"));

    expect(await screen.findByText("We couldn't complete sign-in. Check your browser, then try again.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue in browser" })).toBeTruthy();
  });
});
