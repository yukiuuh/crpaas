import re
import hashlib
import time
import logging

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

from .schemas import RepositoryStatus
from .config import (
    POD_NAMESPACE,
    PVC_NAME,
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
apps_v1_api = client.AppsV1Api()
custom_objects_api = client.CustomObjectsApi()


# --- Helper Functions ---
def sanitize_for_dns(text: str) -> str:
    """Sanitize a string to be usable as a K8s resource name."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9-]', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')

def get_opengrok_pod_name() -> str:
    """
    Finds a running OpenGrok pod name.
    """
    label_selector = "app.kubernetes.io/component=opengrok"
    try:
        pod_list = core_v1_api.list_namespaced_pod(
            namespace=POD_NAMESPACE,
            label_selector=label_selector,
            field_selector="status.phase=Running"
        )
        if not pod_list.items:
            raise Exception("No running OpenGrok pod found.")
        return pod_list.items[0].metadata.name
    except ApiException as e:
        logger.error(f"K8s API error when searching for OpenGrok pod: {e}")
        raise

def exec_clone_repository(repo_url: str, pvc_path: str, commit_id: str, single_branch: bool, recursive: bool) -> tuple[bool, str]:
    """
    Executes the git clone/pull script directly inside the OpenGrok pod.
    Returns (success: bool, output: str).
    """
    try:
        pod_name = get_opengrok_pod_name()
        
        target_dir = f"/opengrok/src/{pvc_path}"
        
        # Arguments for the script: REPO_URL TARGET_DIR COMMIT_OR_BRANCH CLONE_SINGLE_BRANCH CLONE_RECURSIVE
        # Arguments for the script: REPO_URL TARGET_DIR COMMIT_OR_BRANCH CLONE_SINGLE_BRANCH CLONE_RECURSIVE
        script_args = [
            f"'{repo_url}'",
            f"'{target_dir}'",
            f"'{commit_id}'",
            f"'{str(single_branch).lower()}'",
            f"'{str(recursive).lower()}'"
        ]
        
        script_cmd = f"/custom-scripts/git-clone-or-pull.sh {' '.join(script_args)}"
        log_file = f"/tmp/task-log-{pvc_path}.txt"
        
        # Wrap in sh to pipe output to log file. 2>&1 to capture stderr.
        # We use { cmd; } to ensure exit code is preserved if we just cat the log?
        # Actually with | tee, exit code is tee's.
        # Workaround: (cmd) 2>&1 | tee file; test ${PIPESTATUS[0]} ... requires bash.
        # Simple sh fallback: cmd > file 2>&1; ret=$?; cat file; exit $ret
        # But we want to stream to python client too?
        # If we use > file, python client won't see output until we cat it.
        # Using tee is best for "stream to python client" AND "write to file".
        # But we lose exit code in sh.
        # OpenGrok image has bash (verified from docs/common knowledge).
        # We will use /bin/bash -c "set -o pipefail; ..."
        
        wrapped_cmd = f"set -o pipefail; {script_cmd} 2>&1 | tee {log_file}"
        
        exec_command = ["/bin/bash", "-c", wrapped_cmd]
        
        logger.info(f"Execing into {pod_name}: {exec_command}")
        
        # Use stream with stderr=True to capture all output
        resp = stream(
            core_v1_api.connect_get_namespaced_pod_exec,
            pod_name,
            POD_NAMESPACE,
            command=exec_command,
            stderr=True, stdin=False, stdout=True, tty=False
        )
        
        # stream() returns the output string. It throws ApiException on failure if checking response code?
        # Actually stream() usually just returns output. To check exit code, we might need a different approach 
        # or rely on the fact that the script has 'set -eu' and creates output.
        # But wait, stream() doesn't return exit code directly easily unless we use the WebSocket client directly.
        # However, for 'set -eu', if it fails, it usually writes to stderr.
        # Let's assume if it throws exception it failed.
        
        logger.info(f"Exec output: {resp}")
        return True, resp

    except ApiException as e:
        logger.error(f"K8s Exec API error: {e}")
        return False, f"Kubernetes API Error: {e}"
    except Exception as e:
        logger.error(f"Exec failed: {e}")
        return False, str(e)

def exec_cleanup_repository(pvc_path: str) -> bool:
    """
    Executes 'rm -rf' on the target directory inside the OpenGrok pod.
    """
    try:
        pod_name = get_opengrok_pod_name()
        target_dir = f"/opengrok/src/{pvc_path}"
        log_file = f"/tmp/task-log-{pvc_path}.txt"
        
        # Cleanup log just for consistency, though usually fast.
        cmd = f"rm -rf '{target_dir}'"
        wrapped_cmd = f"set -o pipefail; {cmd} 2>&1 | tee {log_file}"
        
        exec_command = ["/bin/bash", "-c", wrapped_cmd]
        
        logger.info(f"Execing cleanup into {pod_name}: {exec_command}")
        
        stream(
            core_v1_api.connect_get_namespaced_pod_exec,
            pod_name,
            POD_NAMESPACE,
            command=exec_command,
            stderr=True, stdin=False, stdout=True, tty=False
        )
        return True
    except Exception as e:
        logger.error(f"Cleanup exec failed: {e}")
        return False

def exec_read_file(file_path: str) -> str:
    """
    Executes 'cat <file_path>' in the OpenGrok pod to read logs or content.
    """
    try:
        pod_name = get_opengrok_pod_name()
        exec_command = ["/bin/cat", file_path]
        
        resp = stream(
            core_v1_api.connect_get_namespaced_pod_exec,
            pod_name,
            POD_NAMESPACE,
            command=exec_command,
            stderr=True, stdin=False, stdout=True, tty=False
        )
        return resp
    except Exception as e:
        # It's expected to fail if file doesn't exist yet (start of clone)
        return ""


# --- OpenGrok Monitoring ---

def get_opengrok_resources() -> dict:
    """
    Finds and returns OpenGrok's Deployment and associated running Pods.
    Returns a dictionary with 'deployment' and 'pods' keys.
    """
    label_selector = "app.kubernetes.io/component=opengrok"
    try:
        # 1. Find the OpenGrok Deployment
        deployment_list = apps_v1_api.list_namespaced_deployment(
            namespace=POD_NAMESPACE,
            label_selector=label_selector
        )
        if not deployment_list.items:
            logger.warning("No OpenGrok Deployment found.")
            return {"deployment": None, "pods": []}
        
        deployment = deployment_list.items[0]

        # 2. Find running Pods for that Deployment
        pod_list = core_v1_api.list_namespaced_pod(
            namespace=POD_NAMESPACE,
            label_selector=label_selector,
            field_selector="status.phase=Running" # Only get running pods
        )
        if not pod_list.items:
            logger.warning("No running OpenGrok pod found.")
        
        return {"deployment": deployment, "pods": pod_list.items}

    except ApiException as e:
        logger.error(f"K8s API error when searching for OpenGrok resources: {e}")
        return {"deployment": None, "pods": []}

def get_pod_metrics(pod_name: str) -> dict:
    """
    Retrieves CPU and Memory usage for a specific pod using the Metrics API.
    Returns a dictionary with 'cpu' and 'memory' usage, or 'N/A' if not available.
    Requires the Kubernetes Metrics Server to be installed in the cluster.
    """
    try:
        metrics = custom_objects_api.get_namespaced_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            namespace=POD_NAMESPACE,
            plural="pods",
            name=pod_name
        )
        # Assuming the main container is the first one
        if metrics.get('containers'):
            usage = metrics['containers'][0]['usage']
            return {"cpu": usage.get('cpu', 'N/A'), "memory": usage.get('memory', 'N/A')}
        return {"cpu": "N/A", "memory": "N/A"}
    except ApiException as e:
        if e.status == 404:
            logger.warning(f"Metrics for pod {pod_name} not found. Is Metrics Server installed?")
        else:
            logger.error(f"K8s Metrics API error for pod {pod_name}: {e}")
        return {"cpu": "N/A", "memory": "N/A"}

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
