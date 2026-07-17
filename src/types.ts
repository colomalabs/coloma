// Shared shapes of backend API payloads. Keep in sync with the Pydantic
// models in backend/ (config.py, tee.py, deploy.py, api.py, profiler.py).

export type ProxyConfig = {
  base_url: string;
  api_key: string;
  capture_bodies: boolean;
  max_body_bytes: number;
  db_path: string;
};

export type SchemaField = {
  name: string;
  type: string;
  validator_code: string;
};

export type ValidationConfig = {
  fields: SchemaField[];
};

export type DeploymentConfig = {
  port: number;
};

export type OptimizationConfig = {
  max_mp: number;
};

export type AppConfig = {
  proxy: ProxyConfig;
  validation: ValidationConfig;
  deployment: DeploymentConfig;
  optimization: OptimizationConfig;
};

export type ConfigStatus = {
  app_config: AppConfig;
};

export type AsyncState = "idle" | "loading" | "success" | "error";

export type ProxyTestResult = {
  ok: boolean;
  models: string[];
  detail: string;
  error: string;
};

export type UpstreamStatus = {
  connected: boolean;
  models: string[];
  model_count: number;
  detail: string;
  error: string;
};

export type RequestSummary = {
  request_id: string;
  started_at: number;
  method: string;
  path: string;
  query_string: string;
  model: string;
  status_code: number | null;
  latency_ms: number | null;
  elapsed_ms: number | null;
  error: string;
  // Bodies are null in list responses; fetch /api/requests/{id} for them and
  // use the has_* flags to know whether there is anything to fetch.
  request_body: string | null;
  original_request_body: string | null;
  response_body: string | null;
  request_truncated: boolean;
  original_request_truncated: boolean;
  response_truncated: boolean;
  validation_issues: string[];
  running: boolean;
  has_request_body: boolean;
  has_original_request_body: boolean;
  has_response_body: boolean;
  verification_status: string;
  verification_resolved: boolean | null;
  verification_response_body: string | null;
  verification_issues: string[];
  verification_error: string;
};

export type RequestsResponse = {
  active: RequestSummary[];
  saved: RequestSummary[];
};

export type GpuStats = {
  index: number;
  name: string;
  utilization_percent: number;
  memory_used_mib: number;
  memory_total_mib: number;
};

export type DockerPullStatus = {
  image: string;
  state: "idle" | "running" | "success" | "error";
  error: string;
};

export type DeployRuntimeStatus = {
  state: "idle" | "starting" | "serving" | "stopping" | "error";
  artifact_id: number | null;
  model_name: string;
  command: string;
  started_at: string;
  uptime_seconds: number;
  container_name: string;
  gpu_busy: boolean;
  error: string;
};

export type DeployStatusResponse = {
  gpus: GpuStats[];
  gpu_error: string;
  docker_images: string[];
  docker_error: string;
  docker_pull: DockerPullStatus;
  runtime: DeployRuntimeStatus;
  default_vllm_image: string;
};

export type PressureTestRequest = {
  prompt_tokens: number;
  num_seqs: number;
  completion_tokens: number;
  ttft_timeout: number;
};

export type RequestSample = {
  prompt_tokens: number;
  completion_tokens: number;
  ttft: number;
  mean_itl: number;
};

export type PressureTestResult = {
  model: string;
  base_url: string;
  started_at: string;
  prompt_tokens: number;
  num_seqs: number;
  completion_tokens: number;
  duration: number;
  samples: RequestSample[];
  median_prompt_tokens: number;
  median_ttft: number;
  p95_ttft: number;
  max_ttft: number;
  average_itl: number;
  system_decoding_throughput: number;
  system_throughput: number;
  failures: number;
  error: string;
};

export type BenchPoint = {
  // The --max-num-seqs the server serving this point was booted with. Absent from artifacts profiled
  // before the sweep ran against several servers, which then plot as a single unlabelled group.
  max_num_seqs?: number;
  series_id: string;
  concurrent_requests: number;
  median_prompt_tokens: number;
  // Tokens each request decoded, and the batch's wall clock. Absent from artifacts profiled before
  // the sweep recorded them; without them the calculator cannot show a measured residual.
  completion_tokens?: number;
  duration?: number;
  // Felt TTFT: the median wait includes queueing behind the batch's other prompts.
  median_ttft: number;
  // Felt ITL: mean token gap over the whole response, slow start under co-resident prefill included.
  average_itl: number;
  system_throughput: number;
};

export type StressTestResult = {
  max_num_seqs: number;
  max_model_len: number;
  median_ttft: number;
};

export type ProfilerDefaults = {
  max_num_seqs_values: number[];
  concurrent_request_values: number[];
};

export type ContextLengthWarning = {
  max_model_len: number;
  reason: "oom" | "stress_timeout";
  max_num_seqs?: number | null;
  // Worded by the backend; the frontend renders it as-is.
  message: string;
};

export type ProfilerStepStatus = "pending" | "running" | "done" | "error" | "skipped" | "cancelled";

export type ProfilerStep = {
  id: number;
  title: string;
  status: ProfilerStepStatus;
  detail: string;
  result: Record<string, unknown>;
  logs: string[];
  error: string;
};

export type ProfilerJobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export type ProfilerConfig = {
  model_name: string;
  api_key: string;
  port: number;
  image_tag: string;
  fp8: boolean;
  gpu_mem: number;
  hf_home: string;
  vllm_home: string;
  extra_vllm_args: string;
  timeout: number;
  ttft_timeout: number;
  stress_test_timeout: number;
  completion_tokens: number;
  // null lets vLLM pick the model's own maximum context length.
  max_model_len: number | null;
  max_num_seqs_values: number[];
  concurrent_request_values: number[];
};

export type ProfilerJobSnapshot = {
  id: string;
  status: ProfilerJobStatus;
  created_at: string;
  updated_at: string;
  config: ProfilerConfig;
  steps: ProfilerStep[];
  bench_points: BenchPoint[];
  stress_tests: StressTestResult[];
  benchmark_timeout_num_seqs: number | null;
  benchmark_skipped: boolean;
  benchmark_skippable: boolean;
  // Sweep points measured out of the total planned across every server; total is 0 until it is known.
  benchmark_progress_done: number;
  benchmark_progress_total: number;
  context_length_capped: number | null;
  context_length_capped_reason: "oom" | "stress_timeout" | null;
  context_length_warnings: ContextLengthWarning[];
  awaiting_oom_recovery: boolean;
  oom_recovery: {
    max_num_seqs: number;
    max_model_len: number;
    retry_max_model_len: number;
    failure_detail: string;
    // True when a --max-num-seqs finished end to end, so retrying should first save its bench as a profile.
    savable: boolean;
  } | null;
  // What the profiled server booted with: they seed and bound the deploy choice.
  kv_token_size: number | null;
  server_max_model_len: number | null;
  // The --max-num-seqs each server was booted at, one chart group per value.
  benchmarked_max_num_seqs_values: number[];
  awaiting_deploy_config: boolean;
  selected_max_num_seqs: number | null;
  selected_max_model_len: number | null;
  docker_command: string;
  artifact_id: number | null;
  error: string;
};

export type ProfilerArtifactSummary = {
  id: number;
  model_name: string;
  vllm_version: string;
  created_at: string;
  docker_command: string;
};

export type ProfilerArtifact = {
  id: number;
  model_name: string;
  vllm_version: string;
  config: Record<string, unknown>;
  created_at: string;
  profiling_results: {
    steps?: Record<string, ProfilerStep>;
    bench_points?: BenchPoint[];
    stress_tests?: StressTestResult[];
    benchmark_timeout_num_seqs?: number | null;
    context_length_capped?: number | null;
    context_length_capped_reason?: "oom" | "stress_timeout" | null;
    context_length_warnings?: ContextLengthWarning[];
    kv_token_size?: number | null;
    server_max_model_len?: number | null;
    selected_max_num_seqs?: number | null;
    selected_max_model_len?: number | null;
    // Names older artifacts stored the KV budget and the chosen context length under.
    max_batch_size?: number | null;
    deploy_max_model_len?: number | null;
  };
  docker_command: string;
};
