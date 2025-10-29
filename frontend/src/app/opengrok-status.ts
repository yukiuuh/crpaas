export interface StorageUsageInfo {
  filesystem: string;
  size_kb: number;
  used_kb: number;
  available_kb: number;
  use_percent: string;
  mountpoint: string;
}

export interface OpenGrokPodStatus {
  pod_name: string;
  pod_status: string;
  pod_ip?: string;
  node_name?: string;
  storage_usage: StorageUsageInfo[];
}