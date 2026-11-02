// DOC-PUBLISH, DOC-ENVIRONMENT-PICKER — the Publish form: the environment it publishes into, and what it says each
// library came to.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PublishForm } from "./PublishForm.tsx";
import type { Definition } from "../catalog.ts";

const publishLibrary = vi.hoisted(() => vi.fn());
vi.mock("../library-client.ts", () => ({ publishLibrary }));

const DEFINITION = { id: "Screening@0.1.0", name: "Screening", version: "0.1.0", title: "Screening",
                     location: "folder:/Workspace/Users/a@example.org/project" } as Definition;
const lib = (name: string, outcome: string, detail: string[] = []) =>
  ({ name, version: "1.0.0", url: `https://x/Library/${name}`, outcome, detail });

beforeEach(() => {
  publishLibrary.mockReset();
});

describe("DOC-PUBLISH PublishForm", () => {
  it("publishes the open folder library into the picked environment, and lists each library's outcome", async () => {
    publishLibrary.mockResolvedValue({ kind: "published", summary: { store: "k.knowledge_store",
      libraries: [lib("Shared", "unchanged"), lib("Screening", "published")] } });
    render(<PublishForm definition={DEFINITION} environmentName="Example" onClose={() => {}} />);
    expect(screen.getByRole("dialog", { name: "Publish Screening" })).toHaveTextContent("into Example");
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(publishLibrary).toHaveBeenCalledWith({
      location: DEFINITION.location, name: "Screening", version: "0.1.0" }));
    const result = await screen.findByRole("list", { name: "What was published" });
    expect(result).toHaveTextContent("Shared 1.0.0 unchanged");
    expect(result).toHaveTextContent("Screening 1.0.0 published");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says nothing was published, and why each library was refused", async () => {
    publishLibrary.mockResolvedValue({ kind: "refused", error: "Shared does not compile",
      summary: { store: "k.knowledge_store", libraries: [lib("Shared", "refused", ["Syntax error at 2:9"]),
                                                         lib("Screening", "not written")] } });
    render(<PublishForm definition={DEFINITION} environmentName="Example" onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was published: Shared does not compile");
    expect(screen.getByRole("list", { name: "What was published" })).toHaveTextContent("Syntax error at 2:9");
  });
});
