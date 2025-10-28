export interface Repository {
  id: number;
  repo_url: string;
  commit_id: string;
  status: string;
  pvc_path: string;
  job_name: string;
  created_at: string;
  updated_at: string;
  expired_at?: string | null;
  last_synced_at?: string | null;
  clone_single_branch: boolean;
  clone_recursive: boolean;
  auto_sync_enabled: boolean;
  auto_sync_schedule: string | null;
}