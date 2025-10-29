import re
import hashlib
import time
import logging

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

from .config import (
    GIT_CLONER_BACKOFF_LIMIT,
    GIT_CLONER_SCRIPT_CONFIGMAP_NAME,
    GIT_CLONER_IMAGE,
    POD_NAMESPACE,
    PVC_NAME,
    SSH_ENABLED,
    SSH_MOUNT_PATH,
    SSH_SECRET_NAME,
    SSH_KEY_FILE_KEY,
)

logger = logging.getLogger(f"uvicorn.{__name__}")

# --- K8s API Client ---
try:
    config.load_incluster_config()
    logger.info("Loaded in-cluster Kubernetes config.")
except config.ConfigException:
    logger.warning("Could not load in-cluster config, loading kube-config.")
    config.load_kube_config()

batch_v1_api = client.BatchV1Api()
core_v1_api = client.CoreV1Api()


# --- Helper Functions ---
def sanitize_for_dns(text: str) -> str:
    """Sanitize a string to be usable as a K8s resource name."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9-]', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')

def create_job_name(repo_url: str, commit_id: str) -> str:
    """Generate a unique Job name (with a timestamp)."""
    
    # Existing logic: generate a hash from the repository URL and commit ID
    hash_object = hashlib.sha1(f"{repo_url}:{commit_id}".encode())
    short_hash = hash_object.hexdigest()[:8]
    
    # 1. Get a shortened repository name
    repo_name = sanitize_for_dns(repo_url.split('/')[-1].replace('.git', ''))

    # 2. Generate a millisecond-precision UNIX timestamp
    # Remove the decimal point to treat it as a number
    timestamp_ms = str(int(time.time() * 1000))
    
    # 3. Generate a Job name combining timestamp and hash
    # Format: fetch-<repo_name>-<timestamp_ms>-<hash>
    full_name = f"fetch-{repo_name}-{timestamp_ms}-{short_hash}"
    
    # Slice the name to not exceed the K8s limit (max 63 characters)
    # (In case the Git repository name is too long, prioritize the prefix while shortening the end)
    max_len = 63
    if len(full_name) > max_len:
        # Example: If repo_name is too long, shorten it while keeping the timestamp and hash
        excess = len(full_name) - max_len
        repo_part = repo_name[:len(repo_name) - excess - 1] # Shorten with a margin
        return f"fetch-{repo_part}-{timestamp_ms}-{short_hash}"[:max_len]
    
    return full_name[:max_len]

def create_job_manifest(job_name: str, repo_url: str, commit_id: str, pvc_path: str, single_branch: bool, recursive: bool) -> client.V1Job:
    """Generate a K8s Job manifest (as a Python object)."""

    job_volumes = [
        client.V1Volume(
            name="source-code-storage",
            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                claim_name=PVC_NAME
            )
        )
        ,
        client.V1Volume(
            name="cloner-script",
            config_map=client.V1ConfigMapVolumeSource(
                name=GIT_CLONER_SCRIPT_CONFIGMAP_NAME,
                default_mode=0o755  # Make the script executable
            )
        )
    ]
    job_volume_mounts = [
        client.V1VolumeMount(
            name="source-code-storage",
            mount_path="/pvc/src"
        ),
        client.V1VolumeMount(name="cloner-script", mount_path="/scripts")
    ]

    if SSH_ENABLED:
        id_rsa_projection = client.V1VolumeProjection(
            secret=client.V1SecretProjection(
                name=SSH_SECRET_NAME,
                items=[
                    client.V1KeyToPath(key=SSH_KEY_FILE_KEY, path="id_rsa", mode=0o400)
                ]
            )
        )
        config_projection = client.V1VolumeProjection(
            config_map=client.V1ConfigMapProjection(
                name=f"ssh-config-ssh-config",
                items=[
                    client.V1KeyToPath(key="config", path="config", mode=0o400)
                ]
            )
        )

        job_volumes.append(
            client.V1Volume(
                name="ssh-volume",
                projected=client.V1ProjectedVolumeSource(
                    default_mode=0o644, # Default permissions for the entire folder
                    sources=[id_rsa_projection, config_projection]
                )
            )
        )

        job_volume_mounts.append(
            client.V1VolumeMount(
                name="ssh-volume",
                mount_path=SSH_MOUNT_PATH,
                read_only=False
            )
        )

    container = client.V1Container(
        name="git-cloner",
        image=GIT_CLONER_IMAGE,
        command=["/scripts/git-clone-or-pull.sh"],
        args=[
            repo_url,
            f"/pvc/src/{pvc_path}",
            commit_id,
            str(single_branch).lower(),
            str(recursive).lower()
        ],
        volume_mounts=job_volume_mounts
    )


    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"app": "crpaas-git-fetcher"}),
        spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[container],
            volumes=job_volumes
        )
    )

    spec = client.V1JobSpec(
        template=template,
        backoff_limit=GIT_CLONER_BACKOFF_LIMIT, # Add retry mechanism
        ttl_seconds_after_finished=3600 # Automatically delete the Job object after 1 hour
    )

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(name=job_name, namespace=POD_NAMESPACE),
        spec=spec
    )
    return job

def create_cleanup_job_manifest(job_name: str, pvc_path: str) -> client.V1Job:
    """
    Generates a K8s Job manifest to delete a directory at the specified PVC path.
    (Uses a simple rm -rf since worktrees are not used)
    """
    # Directory to be deleted (e.g., /pvc/src/github.com/git/git/abc123def)
    target_dir = f"/pvc/src/{pvc_path}"

    cleanup_command = f"""
        set -eux
        TARGET_DIR="{target_dir}"
        
        if [ -d "$TARGET_DIR" ]; then
            echo "Attempting to delete directory: $TARGET_DIR"
            rm -rf "$TARGET_DIR"
            echo "Directory deleted: $TARGET_DIR"
        else
            echo "Target directory not found: $TARGET_DIR. Skipping."
        fi
        
        echo "Cleanup operation complete."
    """
    
    # Use the same Volume Mount, Image, and Namespace as the existing Git Cloner Job
    container = client.V1Container(
        name="git-cleaner",
        image=GIT_CLONER_IMAGE,
        command=["/bin/sh", "-c"],
        args=[cleanup_command],
        volume_mounts=[
            client.V1VolumeMount(
                name="source-code-storage",
                mount_path="/pvc/src"
            )
        ]
    )

    volume = client.V1Volume(
        name="source-code-storage",
        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
            claim_name=PVC_NAME
        )
    )

    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"app": "crpaas-git-cleaner"}),
        spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[container],
            volumes=[volume]
        )
    )

    spec = client.V1JobSpec(
        template=template,
        ttl_seconds_after_finished=300 # Automatically delete the Job object after 5 minutes
    )

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(name=job_name, namespace=POD_NAMESPACE),
        spec=spec
    )
    return job


# --- OpenGrok Monitoring ---

def get_opengrok_pods() -> list[client.V1Pod]:
    """
    Finds and returns a list of running OpenGrok pods using their component label.
    Returns an empty list if no pods are found.
    """
    try:
        pod_list = core_v1_api.list_namespaced_pod(
            namespace=POD_NAMESPACE,
            label_selector="app.kubernetes.io/component=opengrok",
            field_selector="status.phase=Running" # Only get running pods
        )
        if not pod_list.items:
            logger.warning("No running OpenGrok pod found.")
            return []
        return pod_list.items
    except ApiException as e:
        logger.error(f"K8s API error when searching for OpenGrok pod: {e}")
        return []

def get_pod_logs(pod_name: str, tail_lines: int = 200) -> str:
    """
    Retrieves the last N lines of logs from a specified pod.
    """
    try:
        return core_v1_api.read_namespaced_pod_log(
            name=pod_name,
            namespace=POD_NAMESPACE,
            tail_lines=tail_lines
        )
    except ApiException as e:
        logger.error(f"K8s API error when fetching logs for pod {pod_name}: {e}")
        return f"Error fetching logs from Kubernetes: {e.reason}"

def get_storage_usage(pod_name: str) -> list[dict]:
    """
    Executes 'df -Pk' inside the OpenGrok pod and parses the output.
    The -P flag ensures POSIX-compliant output on a single line per filesystem.
    Returns a list of dictionaries, with sizes in kilobytes.
    """
    exec_command = ['/bin/sh', '-c', "df -Pk /opengrok/src /opengrok/data"]
    try:
        resp = stream(
            core_v1_api.connect_get_namespaced_pod_exec,
            pod_name, POD_NAMESPACE,
            command=exec_command,
            stderr=True, stdin=False, stdout=True, tty=False
        )
        
        lines = resp.strip().split('\n')
        parsed_data = []

        for line in lines:
            line = line.strip()
            # Skip header or empty lines
            if not line or line.startswith('Filesystem'):
                continue

            parts = re.split(r'\s+', line)
            if len(parts) < 6:
                continue
            
            # Check if the second column is a digit, indicating a data line
            if not parts[1].isdigit():
                continue

            parsed_data.append({
                "filesystem": parts[0],
                "size_kb": int(parts[1]),
                "used_kb": int(parts[2]),
                "available_kb": int(parts[3]),
                "use_percent": parts[4],
                "mountpoint": parts[5],
            })
        return parsed_data
    except ApiException as e:
        logger.error(f"K8s API error when executing command in pod {pod_name}: {e}")
        return []
    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse 'df -Pk' output: '{resp}'. Error: {e}")
        return []
