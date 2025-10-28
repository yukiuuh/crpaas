export interface RepositoryRequest {
  repo_url: string;
  commit_id: string;
  project_name?: string;
  retention_days?: number;
  clone_single_branch?: boolean;
  clone_recursive?: boolean;
  auto_sync_enabled?: boolean;
  auto_sync_schedule?: string | null;
}