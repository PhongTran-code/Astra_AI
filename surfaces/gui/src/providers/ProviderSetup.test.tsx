// Auth-method segmented choice + show_when field visibility (Bedrock's "Connect with"):
// only the selected method's fields render, and clicking a segment switches them.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { KEY_HELP, ProviderForm, ProviderMark, type ProviderSetupState } from "./ProviderSetup";
import type { ProviderInfo } from "../api";

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));

afterEach(cleanup);

const BEDROCK: ProviderInfo = {
  name: "bedrock",
  title: "AWS Bedrock",
  needs_key: true,
  configured: false,
  values: {},
  suggested_models: [],
  recommended_model: null,
  fields: [
    { key: "region", label: "AWS region", secret: false, required: true, help: "", placeholder: "us-east-1" },
    {
      key: "auth_method",
      label: "Connect with",
      secret: false,
      required: false,
      help: "",
      placeholder: "",
      default: "api_key",
      choices: [
        { value: "api_key", label: "Bedrock API key" },
        { value: "profile", label: "AWS profile" },
        { value: "iam", label: "IAM keys" },
      ],
    },
    { key: "bedrock_api_key", label: "Bedrock API key", secret: true, required: false, help: "", placeholder: "ABSK…", show_when: { auth_method: "api_key" } },
    { key: "aws_profile", label: "AWS profile", secret: false, required: false, help: "", placeholder: "default", show_when: { auth_method: "profile" } },
    { key: "aws_secret_access_key", label: "Secret access key", secret: true, required: false, help: "", placeholder: "", show_when: { auth_method: "iam" } },
  ],
};

const CUSTOM: ProviderInfo = {
  name: "custom",
  title: "Custom (OpenAI-compatible)",
  needs_key: true,
  configured: false,
  values: {},
  suggested_models: [],
  recommended_model: null,
  fields: [
    { key: "base_url", label: "Endpoint", secret: false, required: true, help: "", placeholder: "https://…/v1" },
    { key: "api_key", label: "API key (optional)", secret: true, required: false, help: "", placeholder: "" },
  ],
};

function makePs(
  fields: Record<string, string>,
  setFieldValue = vi.fn(),
  info: ProviderInfo = BEDROCK,
): ProviderSetupState {
  return {
    providers: [info],
    ordered: [info],
    refreshProviders: async () => {},
    sel: info.name,
    info,
    fields,
    setFieldValue,
    dirty: false,
    verify: { state: "idle" },
    showEndpoint: false,
    setShowEndpoint: () => {},
    keylessOk: new Set(),
    credentialed: false,
    savedState: false,
    secretFilled: true,
    openProvider: () => {},
    backToGallery: () => {},
    runTestAndSave: async () => true,
    removeKey: async () => {},
    cancelBackTimer: () => {},
    statusFor: () => null,
    saveField: async () => {},
    fieldSaved: null,
  };
}

describe("ProviderForm auth-method choice", () => {
  it("renders only the selected method's fields", () => {
    render(<ProviderForm ps={makePs({ auth_method: "api_key" })} tp="t" />);
    expect(screen.getByTestId("t-field-bedrock_api_key")).toBeTruthy();
    expect(screen.queryByTestId("t-field-aws_profile")).toBeNull();
    expect(screen.queryByTestId("t-field-aws_secret_access_key")).toBeNull();
    expect(screen.getByTestId("t-choice-auth_method-api_key").getAttribute("aria-checked")).toBe("true");
  });

  it("switching the segment swaps the visible fields", () => {
    const setFieldValue = vi.fn();
    const { rerender } = render(
      <ProviderForm ps={makePs({ auth_method: "api_key" }, setFieldValue)} tp="t" />,
    );
    fireEvent.click(screen.getByTestId("t-choice-auth_method-profile"));
    expect(setFieldValue).toHaveBeenCalledWith("auth_method", "profile");
    rerender(<ProviderForm ps={makePs({ auth_method: "profile" }, setFieldValue)} tp="t" />);
    expect(screen.getByTestId("t-field-aws_profile")).toBeTruthy();
    expect(screen.queryByTestId("t-field-bedrock_api_key")).toBeNull();
  });

  it("iam segment shows the key-pair fields", () => {
    render(<ProviderForm ps={makePs({ auth_method: "iam" })} tp="t" />);
    expect(screen.getByTestId("t-field-aws_secret_access_key")).toBeTruthy();
    expect(screen.queryByTestId("t-field-bedrock_api_key")).toBeNull();
  });
});

describe("Custom provider presentation", () => {
  it("shows the required endpoint directly and keeps the API key optional", () => {
    render(<ProviderForm ps={makePs({}, vi.fn(), CUSTOM)} tp="custom" />);
    expect(screen.getByTestId("custom-field-base_url")).toBeTruthy();
    expect(screen.getByTestId("custom-field-api_key")).toBeTruthy();
    expect(screen.queryByTestId("custom-endpoint-link")).toBeNull();
  });
});

describe("Ark provider presentation", () => {
  it("uses separate BytePlus and Volcengine brand marks", () => {
    const { container, rerender } = render(
      <ProviderMark name="ark" title="BytePlus Ark" />,
    );
    expect(container.querySelector("img")).toBeTruthy();

    rerender(
      <ProviderMark
        name="ark-agent-plan-cn"
        title="Volcengine Ark Agent Plan"
      />,
    );
    expect(container.querySelector("img")).toBeTruthy();
  });

  it("links each provider to its own API key console", () => {
    expect(KEY_HELP.ark.url).toBe(
      "https://console.byteplus.com/ark/region:ark+ap-southeast-1/apiKey",
    );
    expect(KEY_HELP["ark-agent-plan-cn"].url).toContain(
      "advancedActiveKey=agentPlan",
    );
  });
});

describe("OpenCode provider presentation", () => {
  it("renders the built-in layered OpenCode mark without an external asset", () => {
    const { container } = render(
      <ProviderMark name="opencode" title="OpenCode Zen" />,
    );
    expect(container.querySelector("svg")).toBeTruthy();
    expect(screen.getByRole("img", { name: "OpenCode" })).toBeTruthy();
  });
});
