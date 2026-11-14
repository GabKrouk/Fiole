export type Bi = { en: string; fr: string };
export type Msg = Bi & { code?: string };

export interface User {
  id: string;
  email: string;
  display_name: string;
  locale: "fr" | "en";
}

export interface ProjectSummary {
  id: string;
  name: string;
  description: string;
  owner: { id: string; display_name: string; email: string };
  role: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  last_opened_at: string | null;
  url: string;
}

export interface Column {
  id: string;
  name: string;
  type: "numeric" | "text";
}
export type Cell = number | string | null;
export interface Row {
  id: string;
  cells: Record<string, Cell>;
}
export interface Design {
  layout: "wide" | "long";
  group_columns?: string[];
  group?: string | null;
  value?: string | null;
  subject?: string | null;
  technical?: string | null;
  time?: string | null;
  condition?: string | null;
  group_order?: string[];
}
export interface Exclusion {
  row_id: string;
  reason: string;
  at?: string;
}
export interface TableContent {
  columns: Column[];
  rows: Row[];
  design: Design;
  exclusions: Exclusion[];
  transformations?: unknown[];
}
export interface DatasetMeta {
  id: string;
  name: string;
  version: number;
  updated_at: string;
  n_rows: number;
  n_columns: number;
  n_excluded: number;
}
export interface Dataset extends DatasetMeta {
  content: TableContent;
}

export interface AnalysisResultData {
  kind: string;
  title?: Bi;
  status: "ok" | "error";
  errors: Msg[];
  warnings: Msg[];
  groups?: Record<string, any>[];
  test?: {
    name: Bi;
    statistic_name: string;
    statistic: number | null;
    df: number | null;
    p: number;
    p_display: string;
    alternative: string;
    method?: string;
  };
  estimate?: { name: Bi; value: number; ci: [number, number] | null; ci_level?: number };
  effect_size?: { name: Bi; symbol: string; value: number; ci: [number, number] | null; ci_level?: number; ci_method?: Bi; note?: Msg };
  assumptions?: Bi[];
  diagnostics?: { name: Bi; statistic_name: string; statistic: number; p: number; p_display: string; df?: number[] }[];
  diagnostics_note?: Msg;
  method_text?: Bi;
  result_text?: Bi;
  coefficients?: { name: Bi; estimate: number; se: number; t: number; df: number; p: number; p_display: string; ci: [number, number] }[];
  fit?: Record<string, any>;
  n?: number;
  n_pairs?: number;
  engine?: string;
  dataset?: { id: string; name: string; version: number };
  comparisons?: { group1: string; group2: string; p: number; p_display: string; p_raw?: number; p_raw_display?: string; statistic_name?: string; statistic?: number | null; df?: number | null; estimate?: number | null }[];
  family?: { size: number; mode: string; control?: string | null; correction: string; test: string };
  residuals?: { x: number; fitted: number; residual: number }[];
}

export interface Analysis {
  id: string;
  name: string;
  kind: string;
  dataset_id: string;
  config: Record<string, any>;
  version: number;
  updated_at: string;
  last_result: null | {
    id: number;
    dataset_version: number;
    analysis_version: number;
    created_at: string;
    result: AnalysisResultData;
    engine_version: string;
  };
  stale: boolean | null;
  status: "never_run" | "stale" | "up_to_date";
}

export interface FigureEntity {
  id: string;
  name: string;
  kind: string;
  dataset_id: string;
  config: Record<string, any>;
  version: number;
  updated_at: string;
  stale: boolean;
  linked_analyses: string[];
}

export interface ProjectFull extends ProjectSummary {
  permissions: string[];
  datasets: DatasetMeta[];
  analyses: Analysis[];
  figures: FigureEntity[];
}

export interface AnalysisType {
  kind: string;
  available: boolean;
  label: Bi;
  family: string;
  input?: "groups" | "xy";
  min_groups?: number;
  max_groups?: number | null;
  paired?: boolean;
  options?: Record<string, string[]>;
}

export interface ImportPreview {
  columns: Column[];
  rows: Row[];
  missing_count: number;
  warnings: Msg[];
  detected: { delimiter: string | null; decimal: string; header: boolean; sheet?: string; sheets?: string[] };
  upload_id: string | null;
  source_kind?: string;
}

export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any) {
    super(typeof detail === "object" ? detail?.code ?? "error" : String(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function req<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  const init: RequestInit = { method, credentials: "include", headers: {} };
  if (body !== undefined) {
    if (isForm) init.body = body as FormData;
    else {
      init.body = JSON.stringify(body);
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    }
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let detail: any = null;
    try {
      detail = (await r.json()).detail;
    } catch {
      detail = r.statusText;
    }
    throw new ApiError(r.status, detail);
  }
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("application/json") ? r.json() : r.text()) as Promise<T>;
}

export const api = {
  get: <T>(p: string) => req<T>("GET", p),
  post: <T>(p: string, b?: unknown) => req<T>("POST", p, b ?? {}),
  put: <T>(p: string, b: unknown) => req<T>("PUT", p, b),
  patch: <T>(p: string, b: unknown) => req<T>("PATCH", p, b),
  del: <T>(p: string) => req<T>("DELETE", p),
  form: <T>(p: string, f: FormData) => req<T>("POST", p, f, true),
};
