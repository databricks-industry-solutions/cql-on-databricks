// DOC-OPEN-SAVE — New and Save As: where a new library goes by default, and what the form asks the server for.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NewLibraryForm, defaultFolder } from "./NewLibraryForm.tsx";
import type { Definition } from "../catalog.ts";

const createLibrary = vi.hoisted(() => vi.fn());
const fetchLocations = vi.hoisted(() => vi.fn());
vi.mock("../library-client.ts", () => ({ createLibrary, fetchLocations }));

const LOCATIONS = {
  stores: [{ location: "store:k.measures", catalog: "k", schema: "measures" }],
  folders: [{ location: "folder:/Workspace/Users/a@example.org", label: "My folder" }],
};

beforeEach(() => {
  createLibrary.mockReset();
  fetchLocations.mockReset().mockResolvedValue(LOCATIONS);
});

describe("defaultFolder", () => {
  it("is the open folder scope itself, else my folder with the name under it (DOC-SCOPE-OPEN)", () => {
    expect(defaultFolder("folder:/Workspace/Shared/pkgs/", "/Workspace/Users/a", "Screening"))
      .toBe("/Workspace/Shared/pkgs");
    expect(defaultFolder("store:k.measures", "/Workspace/Users/a", "Screening")).toBe("/Workspace/Users/a/Screening");
    expect(defaultFolder(null, null, "Screening")).toBeNull();
  });
});

describe("NewLibraryForm", () => {
  it("creates a new measure in my folder, the path following the name", async () => {
    const onCreated = vi.fn();
    createLibrary.mockResolvedValue({ kind: "saved", location: "folder:/Workspace/Users/a@example.org/Screening",
                                      saved: [], deleted: [], sha256: "x" });
    render(<NewLibraryForm target={{ kind: "new", what: "measure" }} at={null} onCreated={onCreated} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Screening" } });
    await waitFor(() => expect(screen.getByLabelText("Folder")).toHaveValue("/Workspace/Users/a@example.org/Screening"));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(
      "Screening@0.1.0", "folder:/Workspace/Users/a@example.org/Screening"));
    expect(createLibrary).toHaveBeenCalledWith({ location: "folder:/Workspace/Users/a@example.org/Screening",
                                                 name: "Screening", version: "0.1.0", kind: "measure" });
  });

  it("saves a copy into the store being listed, with the CQL as edited", async () => {
    createLibrary.mockResolvedValue({ kind: "saved", location: "store:k.measures", saved: [], deleted: [], sha256: "x" });
    const definition = { id: "CMS1@1.0.0", name: "CMS1", version: "1.0.0", location: "store:k.measures" } as Definition;
    render(<NewLibraryForm target={{ kind: "saveAs", definition, cql: "library CMS1 version '1.0.0'\n" }}
      at="store:k.measures" onCreated={() => {}} onCancel={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("Store")).toHaveValue("store:k.measures"));
    fireEvent.click(screen.getByRole("button", { name: "Save copy" }));
    await waitFor(() => expect(createLibrary).toHaveBeenCalledWith({
      location: "store:k.measures", name: "CMS1Copy", version: "0.1.0", cql: "library CMS1 version '1.0.0'\n",
      copyOf: { location: "store:k.measures", name: "CMS1", version: "1.0.0" } }));
  });

  it("says why nothing was created", async () => {
    createLibrary.mockResolvedValue({ kind: "conflict", error: "Screening 0.1.0 is already in /Workspace/x" });
    render(<NewLibraryForm target={{ kind: "new", what: "library" }} at="folder:/Workspace/x" onCreated={() => {}}
      onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Screening" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not created: Screening 0.1.0 is already in /Workspace/x");
  });
});
