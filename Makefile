# Makefile for building and pushing Docker images for crpaas

# Use podman if you prefer, e.g., `make DOCKER_CMD=podman all`
DOCKER_CMD ?= docker

# Registry URL. Override if needed, e.g., `make REGISTRY=myregistry.com all`
# This default matches the insecure-registries setting in skaffold.yaml
REGISTRY ?= localhost:5000

# Image names
MANAGER_IMAGE := $(REGISTRY)/crpaas-manager
UI_IMAGE      := $(REGISTRY)/crpaas-ui

# Image tag. Default is 'latest'. Override with `make TAG=v1.0.0 all`
TAG ?= latest

.PHONY: all build push build-manager build-ui push-manager push-ui help

all: build push

build: build-manager build-ui ## Build both manager and ui images

push: push-manager push-ui ## Push both manager and ui images

build-manager: ## Build the backend manager image
	@echo "Building $(MANAGER_IMAGE):$(TAG)..."
	$(DOCKER_CMD) build -t $(MANAGER_IMAGE):$(TAG) -f ./backend/Dockerfile ./backend

build-ui: ## Build the frontend ui image
	@echo "Building $(UI_IMAGE):$(TAG)..."
	$(DOCKER_CMD) build -t $(UI_IMAGE):$(TAG) -f ./frontend/Dockerfile ./frontend

push-manager: ## Push the backend manager image
	@echo "Pushing $(MANAGER_IMAGE):$(TAG)..."
	$(DOCKER_CMD) push $(MANAGER_IMAGE):$(TAG)

push-ui: ## Push the frontend ui image
	@echo "Pushing $(UI_IMAGE):$(TAG)..."
	$(DOCKER_CMD) push $(UI_IMAGE):$(TAG)

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'