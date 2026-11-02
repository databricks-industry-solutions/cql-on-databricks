// RUN-ADDRESS-LOCATION, DOC-OPEN-SAVE — the Open menu chooses where the catalog reads from, and opening a library
// from a folder sets the location to that folder.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Definition } from "../catalog.ts";
import { Navigator, locationLabel } from "./Navigator.tsx";

const OPEN: Definition = {
  id: "Measure@1.0.0", name: "Measure", version: "1.0.0", location: "store:k.measures", title: "A measure",
  cat: "measure", src: "v1.0.0", status: "pub", stat: "", statk: "", mine: false, meta: "", desc: "",
};

const FOLDER = "/Workspace/Users/a@example.org/ExamplePackage";

function serve(routes: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const body = routes[url];
    if (body === undefined) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }));
}

describe("the Open menu's location (DOC-OPEN-SAVE)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("names a location by its store or its folder's last segment", () => {
    expect(locationLabel(null)).toBe("All tagged stores");
    expect(locationLabel("store:k.measures")).toBe("k.measures");
    expect(locationLabel(`folder:${FOLDER}`)).toBe("ExamplePackage");
  });

  it("lists a store, and opens a library from a folder at that folder", async () => {
    serve({
      "/api/locations": { stores: [{ location: "store:k.measures", catalog: "k", schema: "measures" }],
                          folders: [{ location: "folder:/Workspace/Users/a@example.org", label: "My folder" }] },
      "/api/folders?path=%2FWorkspace%2FUsers%2Fa%40example.org": {
        path: "/Workspace/Users/a@example.org", location: "folder:/Workspace/Users/a@example.org",
        folders: [{ name: "ExamplePackage", path: FOLDER }], libraries: [] },
      [`/api/folders?path=${encodeURIComponent(FOLDER)}`]: {
        path: FOLDER, location: `folder:${FOLDER}`, folders: [],
        libraries: [{ id: { name: "Measure", version: "1.0.0" }, location: `folder:${FOLDER}`, kind: "measure",
                      status: "draft", title: "Folder measure", metadata: { description: "", tags: [], author: "" },
                      updated: "" }] },
    });
    const onOpenAt = vi.fn();
    render(<Navigator definition={OPEN} definitions={[OPEN]} onOpenDefinition={() => {}} at={null}
      onOpenAt={onOpenAt} />);
    fireEvent.click(screen.getByRole("button", { name: /A measure/ }));
    fireEvent.click(screen.getByRole("button", { name: /Location/ }));

    fireEvent.click(await screen.findByRole("button", { name: /k\.measures/ }));
    expect(onOpenAt).toHaveBeenLastCalledWith("store:k.measures");

    fireEvent.click(screen.getByRole("button", { name: /Location/ }));
    fireEvent.click(await screen.findByRole("button", { name: /My folder/ }));
    fireEvent.click(await screen.findByRole("button", { name: /ExamplePackage/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Folder measure/ }));
    await waitFor(() => expect(onOpenAt).toHaveBeenLastCalledWith(`folder:${FOLDER}`, "Measure@1.0.0"));
  });
});
