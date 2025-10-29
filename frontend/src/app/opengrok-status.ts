export interface StorageUsageInfo {
  filesystem: string;
  size_kb: number;
  used_kb: number;
  available_kb: number;
  use_percent: string;
  mountpoint: string;
}

export interface OpenGrokDeploymentStatus {
  name: string;
  replicas: number;
  ready_replicas: number;
  available_replicas: number;
  unavailable_replicas: number;
  updated_replicas: number;
}

export interface OpenGrokPodStatus {
  pod_name: string;
  pod_status: string;
  pod_ip?: string;
  node_name?: string;
  cpu_usage?: string | null;
  memory_usage?: string | null;
  storage_usage: StorageUsageInfo[];
}

export interface OpenGrokStatusResponse {
  deployment_status?: OpenGrokDeploymentStatus | null;
  pod_statuses: OpenGrokPodStatus[];
}