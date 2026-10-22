import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SearchModal } from "./SearchModal";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("SearchModal Team search", () => {
  it("searches ACL-scoped Team records and opens the selected result", async () => {
    const fetchStub = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        agents: {
          results: [{ cursor: "0", agent_id: "agent-1", display_name: "Builder", handle: "builder", status: "ready" }],
          next_cursor: "1",
          has_more: false,
        },
      }),
    } as Response));
    vi.stubGlobal("fetch", fetchStub);
    const onSelectTeam = vi.fn();

    render(
      <SearchModal
        sessions={[]}
        onSelect={vi.fn()}
        onSelectTeam={onSelectTeam}
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("Search chats"), { target: { value: "Builder" } });

    const result = await screen.findByText("Builder");
    fireEvent.click(result);
    await waitFor(() => expect(onSelectTeam).toHaveBeenCalledWith(expect.objectContaining({
      kind: "agents",
      agent_id: "agent-1",
    })));
    expect(String((fetchStub.mock.calls[0] as unknown as [string])[0])).toContain("kind=agents");
  });
});
