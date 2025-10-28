import os

# --- Environment Variables (passed from Helm chart) ---
PVC_NAME = os.environ.get("SOURCE_CODE_PVC_NAME")
GIT_CLONER_IMAGE = os.environ.get("GIT_CLONER_IMAGE")
GIT_CLONER_BACKOFF_LIMIT = int(os.environ.get("GIT_CLONER_BACKOFF_LIMIT", 3))
GIT_CLONER_SCRIPT_CONFIGMAP_NAME = os.environ.get("GIT_CLONER_SCRIPT_CONFIGMAP_NAME")
POD_NAMESPACE = os.environ.get("POD_NAMESPACE")
OPEN_GROK_REINDEX_SERVICE_HOST = os.environ.get("OPEN_GROK_REINDEX_SERVICE_HOST")
OPEN_GROK_REINDEX_PORT = os.environ.get("OPEN_GROK_REINDEX_PORT")
SSH_SECRET_NAME = os.environ.get("GIT_SSH_SECRET_NAME")
SSH_KEY_FILE_KEY = os.environ.get("GIT_SSH_KEY_FILE_KEY")
OPEN_GROK_BASE_URL = os.environ.get("OPEN_GROK_BASE_URL")

OPEN_GROK_REINDEX_URL = f"http://{OPEN_GROK_REINDEX_SERVICE_HOST}:{OPEN_GROK_REINDEX_PORT}/reindex"
SSH_ENABLED = SSH_SECRET_NAME and SSH_KEY_FILE_KEY
SSH_MOUNT_PATH = "/root/.ssh"

# --- Database ---
DB_PATH = "/data/manager.db"

# --- Worker ---
WATCH_INTERVAL_SEC = 5
AUTO_SYNC_INTERVAL_SEC = 60 # Check for scheduled syncs every 60 seconds
