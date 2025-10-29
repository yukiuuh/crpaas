# CRPaaS (Helm Chart)

This chart deploys a "Code Reading Platform as a Service" (CRPaaS) on Kubernetes. It provides a self-service web UI for users to manage which Git repositories are indexed by OpenGrok, enabling efficient source code reading and searching.

## Features

- **Web UI for Repository Management**: An intuitive interface to add, list, and delete repositories.
- **Dynamic Indexing**: Add new repositories and have them automatically cloned and indexed by OpenGrok.
- **Specific Revisions**: Pin repositories to a specific branch, tag, or commit hash.
- **Sync Repositories**: Re-sync existing repositories to fetch the latest changes.
- **Configurable Retention**: Set an expiration date for each repository to automatically clean up old source code.
- **Private Repository Support**: Use SSH keys to clone private Git repositories.
- **Advanced Clone Options**: Optimize cloning with options like `--single-branch` and `--recursive` for submodules.
- **Real-time Status & Logs**: Monitor the status of cloning jobs (Pending, Running, Completed, Failed) and view logs directly from the UI.

## Architecture

The system consists of three main components deployed as separate pods:

1.  **Frontend (crpaas-manager-ui)**: The Angular-based web interface that users interact with.
2.  **Backend (crpaas-manager)**: A FastAPI application that manages the database of repositories and creates Kubernetes Jobs for Git operations.
3.  **OpenGrok**: The core OpenGrok application that serves the indexed source code.

When a user adds a repository, the backend creates a temporary **Git Cloner Job** that clones the source code into a shared `ReadWriteMany` volume. Once the job is complete, the backend triggers the OpenGrok pod to re-index its sources.

## Prerequisites

1.  **Kubernetes 1.21+**
2.  **Helm 3.2+**
3.  A **ReadWriteMany (RWX) StorageClass** must be available in the cluster for the shared source code volume (e.g., NFS, CephFS, EFS, Filestore).
4.  A **ReadWriteOnce (RWO) StorageClass** must be available for the OpenGrok index data and the Manager's database.
5.  The Frontend (`crpaasUi.image`) and Backend (`crpaasManager.image`) container images must be built and accessible from your cluster.

## Installation

1.  Copy `values.yaml` locally and edit it to match your environment.

    ```bash
    curl https://raw.githubusercontent.com/yukiuuh/crpaas/refs/heads/master/charts/crpaas/values.yaml > my-values.yaml
    ```

2.  Ensure the `storage`, `ingress`, and image repository sections in `my-values.yaml` are configured correctly for your environment.

3.  Deploy using Helm.

    ```bash
    # (Example) Deploy with the name 'my-crpaas' into the 'crpaas' namespace
    helm install my-crpaas oci://ghcr.io/yukiuuh/helm-charts/crpaas \
      -n crpaas --create-namespace \
      -f my-values.yaml
    ```

## Configuration

The following are some of the key parameters to configure in your `my-values.yaml`:

| Parameter | Description | Default |
|---|---|---|
| `opengrok.image.repository` | The container image for OpenGrok. | `opengrok/docker` |
| `opengrok.image.tag` | The tag for the OpenGrok container image. | `latest` |
| `crpaasUi.image.repository` | The container image for the frontend UI. | `ghcr.io/yukiuuh/crpaas-ui` |
| `crpaasUi.image.tag` | The tag for the frontend UI container image. | `v0.0.2` |
| `crpaasManager.image.repository` | The container image for the backend manager. | `ghcr.io/yukiuuh/crpaas-manager` |
| `crpaasManager.image.tag` | The tag for the backend manager container image. | `v0.0.2` |
| `opengrok.ingress.enabled` | Enable Ingress for the OpenGrok UI. | `false` |
| `opengrok.ingress.hosts[0].host` | Hostname for the OpenGrok UI. | `opengrok.local` |
| `crpaasUi.ingress.enabled` | Enable Ingress for the frontend UI. | `false` |
| `crpaasUi.ingress.hosts[0].host` | Hostname for the frontend UI. | `crpaas-manager-ui.local` |
| `crpaasManager.ingress.enabled` | Enable Ingress for the backend manager. | `false` |
| `crpaasManager.ingress.hosts[0].host` | Hostname for the backend manager. | `crpaas-manager.local` |
| `crpaasManager.gitCloner.image.repository` | The image to use for the `git clone` jobs. | `alpine/git` |
| `crpaasManager.gitCloner.image.tag` | The tag for the `git clone` job image. | `latest` |
| `crpaasManager.gitCloner.backoffLimit` | Number of retries before marking the git-clone Job as failed. | `3` |
| `crpaasManager.gitSsh.secretName` | The name of the Kubernetes secret containing the SSH private key for private repositories. | `git-ssh-key-secret` |
| `crpaasManager.gitSsh.sshKeyFileKey` | The key within the secret that contains the SSH private key file. | `id_rsa` |
| `storage.source.storageClassName` | The `StorageClass` for the shared source code volume (must support RWX). | `longhorn` |
| `storage.source.size` | The size of the source code volume. | `50Gi` |
| `storage.data.storageClassName` | The `StorageClass` for the OpenGrok index data volume (RWO is sufficient). | `longhorn` |
| `storage.data.size` | The size of the OpenGrok index data volume. | `50Gi` |
| `storage.manager.storageClassName` | The `StorageClass` for the manager's database volume (RWO is sufficient). | `longhorn` |
| `storage.manager.size` | The size of the manager's database volume. | `1Gi` |
| `opengrok.resources` | CPU/Memory resource requests and limits for OpenGrok. | `{}` |
| `crpaasUi.resources` | CPU/Memory resource requests and limits for the frontend UI. | `{}` |
| `crpaasManager.resources` | CPU/Memory resource requests and limits for the backend manager. | `{}` |
| `rbac.create` | Specifies whether RBAC resources should be created. | `true` |
| `serviceAccount.create` | Specifies whether a service account should be created. | `true` |
| `serviceAccount.name` | The name of the service account to use. If not set and `create` is true, a name is generated. | `""` |

### Private Repositories via SSH

To use private repositories, you must create a Kubernetes secret containing your SSH private key.

1.  Create the secret:
    ```bash
    # The --from-file key ('id_rsa') must match crpaasManager.gitSsh.sshKeyFileKey in values.yaml
    kubectl create secret generic git-ssh-key-secret \
      --from-file=id_rsa=/path/to/your/ssh/private_key \
      -n crpaas
    ```

2.  Ensure the `crpaasManager.gitSsh.secretName` in your `values.yaml` matches the secret name (`git-ssh-key-secret`).