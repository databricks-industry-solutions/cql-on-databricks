// APP-EXECUTION-PILL — the ONE execution control: a short menu with the run STATUS at its head,
// ENVIRONMENT (a submenu of the environments the user can see), COMPUTE (a submenu of SQL warehouses), and
// Automatic/Manual update options, topped with one REFRESH button. The trigger shows the dot and caret; the
// environment, warehouse and update mode are in its tooltip.
// APP-UPDATE-MODE / APP-COMPUTE-TARGET / DOC-ENVIRONMENT-PICKER.
//
// ONE ACT: Automatic covers compute-missing; Refresh is the one explicit act and recomputes everything.
// Automatic and Manual are two marked options, not a checkbox (unchecked state reads ambiguously).
// Compute is a real choice: a measure runs as SQL on a warehouse (`RUN-STATEMENT-EXEC`),
// and the reader picks which warehouse.
//
// The execution PARAMETERS stay in the bar (`ExecParamBar`): they are changed while reading.

import type React from "react";
import { PillMenu, type PillSection, type PillStatus } from "./PillMenu.tsx";
import type { EnvironmentRecord, WarehouseRecord } from "../wire.ts";
import {
  AUTOMATIC, EXEC_MODES, execModeInfo, type ExecKind, type ExecMode, type UpdateMode,
} from "./model.ts";

function kindStatus(kind: ExecKind): PillStatus {
  return kind === "none" ? "none" : kind === "synthetic" ? "info" : "live";
}

/** DOC-ENVIRONMENT-PICKER — every environment the pill can select is live: a tagged catalog the user can see. */
const ENVIRONMENT_STATUS: PillStatus = "live";

/** DOC-ENVIRONMENT-PICKER — what this environment holds, in the words of its catalog's comment, and whether it is
 *  the default. No subtitle is synthesized from the record's other fields. */
function environmentBlurb(r: EnvironmentRecord): string | undefined {
  return [r.default ? "Default" : "", r.description ?? ""].filter(Boolean).join(" · ") || undefined;
}

/** APP-COMPUTE-TARGET — a warehouse row's subtitle: its state, and whether it is the deployment's. */
function warehouseBlurb(w: WarehouseRecord, defaultId: string | null): string | undefined {
  const parts = [w.id === defaultId ? "Default" : "", w.state ? w.state.toLowerCase() : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : undefined;
}

export function ComputeMenu({ mode, running, compiling = false, progress = null, stageBadge, environments = [],
                             environment = null,
                             warehouses = [], defaultWarehouseId = null, warehouseId = null, warehouseError = null,
                             updateMode, runDisabled = false, onModeChange, onEnvironmentChange, onWarehouseChange,
                             onUpdateModeChange, onRefresh }: {
  mode: ExecMode;
  running: boolean;
  compiling?: boolean;
  /** APP-COMPUTE-ACTIVITY — 0–1 through the run, for the collapsed trigger's hairline. Passed
   *  through untouched: the pill draws progress, it does not decide what progress means. */
  progress?: number | null;
  stageBadge?: React.ReactNode;
  /** DOC-ENVIRONMENT-PICKER — the environments the signed-in user can see (`GET /api/environments`). */
  environments?: EnvironmentRecord[];
  /** The environment runs are in; null while none is computing (None). */
  environment?: string | null;
  /** APP-COMPUTE-TARGET — the warehouses the signed-in user can see (`GET /api/warehouses`). */
  warehouses?: WarehouseRecord[];
  defaultWarehouseId?: string | null;
  /** The chosen warehouse ID; null means the deployment default. */
  warehouseId?: string | null;
  /** Why the warehouse list is unavailable, shown in the Compute section. */
  warehouseError?: string | null;
  updateMode: UpdateMode;
  /** No environment, no CQL yet, or an operation already in flight. */
  runDisabled?: boolean;
  onModeChange: (m: ExecMode) => void;
  onEnvironmentChange?: (name: string) => void;
  onWarehouseChange?: (id: string) => void;
  onUpdateModeChange: (m: UpdateMode) => void;
  /** APP-UPDATE-MODE — Refresh: recompute with a computed_since cutoff (RUN-CLIENT-REFRESH-RESUME). */
  onRefresh?: () => void;
}) {
  const names = new Set(environments.map((r) => r.name));
  const modeOptions = EXEC_MODES.map((m) => ({ id: m.id, label: m.label, blurb: m.blurb, status: kindStatus(m.kind) }));
  const environmentOptions = environments.map((r) => ({ id: r.name, label: r.name, blurb: environmentBlurb(r),
                                                        status: ENVIRONMENT_STATUS }));
  const activeWarehouse = warehouseId ?? defaultWarehouseId;

  const sections: PillSection[] = [
    {
      kind: "submenu",
      label: "Environment",
      value: environment ?? mode,
      options: [...modeOptions, ...environmentOptions],
      onChange: (id) => (names.has(id) ? onEnvironmentChange?.(id) : onModeChange(id as ExecMode)),
    },
    {
      kind: "submenu",
      label: "Compute",
      value: activeWarehouse ?? "",
      options: warehouses.map((w) => ({ id: w.id, label: w.name ?? w.id, blurb: warehouseBlurb(w, defaultWarehouseId) })),
      onChange: (id) => onWarehouseChange?.(id),
      empty: warehouseError ? `SQL warehouses unavailable: ${warehouseError}` : "No SQL warehouses you can use.",
    },
    // APP-UPDATE-MODE — one checkable item. Unchecked is Manual: nothing runs until Refresh is pressed.
    {
      kind: "check",
      label: AUTOMATIC.label,
      blurb: AUTOMATIC.blurb,
      checked: updateMode === "live",
      onChange: (on) => onUpdateModeChange(on ? "live" : "paused"),
    },
    {
      kind: "actions",
      label: "Refresh",
      actions: [
        { id: "refresh", label: "Refresh", blurb: "Recompute every define from the current data.",
          disabled: runDisabled, onPick: () => onRefresh?.() },
      ],
    },
  ];

  const processing = compiling || running;
  const engaged = mode !== "static" || environment !== null;
  const sourceLabel = environment;
  const warehouseLabel = activeWarehouse
    ? (warehouses.find((w) => w.id === activeWarehouse)?.name ?? activeWarehouse) : null;
  const updateLabel = updateMode === "live" ? "Automatic" : "Manual";
  const title = engaged
    ? [sourceLabel ?? execModeInfo(mode).label, warehouseLabel, updateLabel].filter(Boolean).join(" · ")
    : "None — no computed data";

  return (
    <PillMenu status={engaged ? "live" : "none"} running={processing} progress={progress}
      ariaLabel="Execution control" title={title} sections={sections} stageBadge={stageBadge} />
  );
}
