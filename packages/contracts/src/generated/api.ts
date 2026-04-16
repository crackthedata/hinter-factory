/**
 * Generated from `packages/contracts/openapi/openapi.yaml` via openapi-typescript.
 * Regenerate: `pnpm --filter @hinter/contracts generate`
 */
export interface paths {
  "/v1/documents/upload": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: operations["uploadDocuments"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/documents": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: operations["listDocuments"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/documents/{document_id}": {
    parameters: { query?: never; header?: never; path: { document_id: string }; cookie?: never };
    get: operations["getDocument"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/documents/facets/metadata-keys": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: operations["listMetadataFacetKeys"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/documents/facets/metadata-values": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: operations["listMetadataFacetValues"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/tags": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: operations["listTags"];
    put?: never;
    post: operations["createTag"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/labeling-functions": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: operations["listLabelingFunctions"];
    put?: never;
    post: operations["createLabelingFunction"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/labeling-functions/{labeling_function_id}": {
    parameters: {
      query?: never;
      header?: never;
      path: { labeling_function_id: string };
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete: operations["deleteLabelingFunction"];
    options?: never;
    head?: never;
    patch: operations["updateLabelingFunction"];
    trace?: never;
  };
  "/v1/labeling-functions/{labeling_function_id}/preview": {
    parameters: {
      query?: never;
      header?: never;
      path: { labeling_function_id: string };
      cookie?: never;
    };
    get?: never;
    put?: never;
    post: operations["previewLabelingFunction"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/lf-runs": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: operations["createLfRun"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/lf-runs/{run_id}": {
    parameters: { query?: never; header?: never; path: { run_id: string }; cookie?: never };
    get: operations["getLfRun"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/lf-runs/{run_id}/matrix": {
    parameters: { query?: never; header?: never; path: { run_id: string }; cookie?: never };
    get: operations["exportLfMatrix"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/probabilistic-labels": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: operations["listProbabilisticLabels"];
    put?: never;
    post: operations["upsertProbabilisticLabel"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/v1/gold-labels": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: operations["listGoldLabels"];
    put?: never;
    post: operations["createGoldLabel"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
}

export type webhooks = Record<string, never>;

export interface components {
  schemas: {
    LengthBucket: "short" | "medium" | "long";
    Document: {
      id: string;
      text: string;
      metadata: Record<string, unknown>;
      char_length: number;
      created_at: string;
    };
    DocumentListResponse: {
      items: components["schemas"]["Document"][];
      total: number;
    };
    DocumentIngestResult: {
      inserted: number;
      skipped: number;
      errors: string[];
    };
    Tag: {
      id: string;
      name: string;
      taxonomy_version: string;
      created_at: string;
    };
    TagCreate: {
      name: string;
      taxonomy_version?: string;
    };
    LabelingFunctionType: "regex" | "keywords" | "structural" | "zeroshot" | "llm_prompt";
    LabelingFunction: {
      id: string;
      tag_id: string;
      name: string;
      type: components["schemas"]["LabelingFunctionType"];
      config: Record<string, unknown>;
      enabled: boolean;
      created_at: string;
    };
    LabelingFunctionCreate: {
      tag_id: string;
      name: string;
      type: components["schemas"]["LabelingFunctionType"];
      config: Record<string, unknown>;
      enabled?: boolean;
    };
    LabelingFunctionUpdate: {
      name?: string;
      config?: Record<string, unknown>;
      enabled?: boolean;
    };
    LabelingFunctionPreviewRequest: {
      limit?: number;
      document_ids?: string[];
    };
    LabelingFunctionPreviewRow: {
      document_id: string;
      vote: components["schemas"]["LfVote"];
      text_preview: string;
    };
    LabelingFunctionPreviewResponse: {
      rows: components["schemas"]["LabelingFunctionPreviewRow"][];
    };
    LfVote: -1 | 0 | 1;
    LfRunStatus: "pending" | "running" | "completed" | "failed";
    LfRunCreate: {
      tag_id: string;
      labeling_function_ids: string[];
    };
    LfRun: {
      id: string;
      tag_id: string;
      labeling_function_ids: string[];
      status: components["schemas"]["LfRunStatus"];
      error?: string | null;
      documents_scanned?: number;
      votes_written?: number;
      created_at: string;
      completed_at?: string | null;
    };
    SparseLabelMatrix: {
      run_id: string;
      document_ids: string[];
      labeling_function_ids: string[];
      entries: components["schemas"]["SparseLabelMatrixEntry"][];
    };
    SparseLabelMatrixEntry: {
      d: number;
      l: number;
      v: components["schemas"]["LfVote"];
    };
    ProbabilisticLabel: {
      document_id: string;
      tag_id: string;
      probability: number;
      conflict_score?: number | null;
      entropy?: number | null;
      updated_at?: string;
    };
    ProbabilisticLabelUpsert: {
      document_id: string;
      tag_id: string;
      probability: number;
      conflict_score?: number | null;
      entropy?: number | null;
    };
    GoldLabel: {
      id: string;
      document_id: string;
      tag_id: string;
      value: components["schemas"]["LfVote"];
      note?: string | null;
      created_at: string;
    };
    GoldLabelCreate: {
      document_id: string;
      tag_id: string;
      value: components["schemas"]["LfVote"];
      note?: string | null;
    };
  };
  responses: never;
  parameters: never;
  requestBodies: never;
  headers: never;
  pathItems: never;
}

export type $defs = Record<string, never>;

export interface operations {
  uploadDocuments: {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    requestBody: {
      content: {
        "multipart/form-data": {
          file: string;
          text_column?: string;
          id_column?: string;
        };
      };
    };
    responses: {
      201: { content: { "application/json": components["schemas"]["DocumentIngestResult"] } };
    };
  };
  listDocuments: {
    parameters: {
      query?: {
        q?: string;
        length_bucket?: ("short" | "medium" | "long")[];
        metadata_key?: string;
        metadata_value?: string;
        limit?: number;
        offset?: number;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    responses: {
      200: { content: { "application/json": components["schemas"]["DocumentListResponse"] } };
    };
  };
  getDocument: {
    parameters: {
      query?: never;
      header?: never;
      path: { document_id: string };
      cookie?: never;
    };
    responses: {
      200: { content: { "application/json": components["schemas"]["Document"] } };
      404: { content?: never };
    };
  };
  listMetadataFacetKeys: {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    responses: { 200: { content: { "application/json": string[] } } };
  };
  listMetadataFacetValues: {
    parameters: {
      query: { key: string; limit?: number };
      header?: never;
      path?: never;
      cookie?: never;
    };
    responses: { 200: { content: { "application/json": string[] } } };
  };
  listTags: {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    responses: { 200: { content: { "application/json": components["schemas"]["Tag"][] } } };
  };
  createTag: {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    requestBody: {
      content: { "application/json": components["schemas"]["TagCreate"] };
    };
    responses: { 201: { content: { "application/json": components["schemas"]["Tag"] } } };
  };
  listLabelingFunctions: {
    parameters: { query?: { tag_id?: string } };
    responses: {
      200: { content: { "application/json": components["schemas"]["LabelingFunction"][] } };
    };
  };
  createLabelingFunction: {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    requestBody: {
      content: { "application/json": components["schemas"]["LabelingFunctionCreate"] };
    };
    responses: {
      201: { content: { "application/json": components["schemas"]["LabelingFunction"] } };
    };
  };
  updateLabelingFunction: {
    parameters: {
      query?: never;
      header?: never;
      path: { labeling_function_id: string };
      cookie?: never;
    };
    requestBody: {
      content: { "application/json": components["schemas"]["LabelingFunctionUpdate"] };
    };
    responses: {
      200: { content: { "application/json": components["schemas"]["LabelingFunction"] } };
    };
  };
  deleteLabelingFunction: {
    parameters: {
      query?: never;
      header?: never;
      path: { labeling_function_id: string };
      cookie?: never;
    };
    responses: { 204: { content?: never } };
  };
  previewLabelingFunction: {
    parameters: {
      query?: never;
      header?: never;
      path: { labeling_function_id: string };
      cookie?: never;
    };
    requestBody?: {
      content: { "application/json": components["schemas"]["LabelingFunctionPreviewRequest"] };
    };
    responses: {
      200: {
        content: { "application/json": components["schemas"]["LabelingFunctionPreviewResponse"] };
      };
    };
  };
  createLfRun: {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    requestBody: { content: { "application/json": components["schemas"]["LfRunCreate"] } };
    responses: { 202: { content: { "application/json": components["schemas"]["LfRun"] } } };
  };
  getLfRun: {
    parameters: {
      query?: never;
      header?: never;
      path: { run_id: string };
      cookie?: never;
    };
    responses: { 200: { content: { "application/json": components["schemas"]["LfRun"] } } };
  };
  exportLfMatrix: {
    parameters: {
      query?: never;
      header?: never;
      path: { run_id: string };
      cookie?: never;
    };
    responses: {
      200: { content: { "application/json": components["schemas"]["SparseLabelMatrix"] } };
    };
  };
  listProbabilisticLabels: {
    parameters: {
      query?: { tag_id?: string; limit?: number; offset?: number };
      header?: never;
      path?: never;
      cookie?: never;
    };
    responses: {
      200: { content: { "application/json": components["schemas"]["ProbabilisticLabel"][] } };
    };
  };
  upsertProbabilisticLabel: {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    requestBody: {
      content: { "application/json": components["schemas"]["ProbabilisticLabelUpsert"] };
    };
    responses: {
      200: { content: { "application/json": components["schemas"]["ProbabilisticLabel"] } };
    };
  };
  listGoldLabels: {
    parameters: {
      query?: { document_id?: string; document_ids?: string[]; tag_id?: string };
      header?: never;
      path?: never;
      cookie?: never;
    };
    responses: {
      200: { content: { "application/json": components["schemas"]["GoldLabel"][] } };
    };
  };
  createGoldLabel: {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    requestBody: {
      content: { "application/json": components["schemas"]["GoldLabelCreate"] };
    };
    responses: {
      201: { content: { "application/json": components["schemas"]["GoldLabel"] } };
    };
  };
}

export type SchemaName = keyof components["schemas"];
export type Schema<T extends SchemaName> = components["schemas"][T];
