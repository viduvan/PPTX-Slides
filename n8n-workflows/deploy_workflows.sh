#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Automated n8n Workflows Deployment Script
# ═══════════════════════════════════════════════════════════
set -e

# Configuration
N8N_CONTAINER="pptx-slides-n8n"
WORKFLOW_DIR="/home/vietpv/Desktop/pptx-slides/n8n-workflows"
FILES=(
  "02_analysis_pipeline.json"
  "03_writing_pipeline.json"
  "04_design_pipeline.json"
  "05_export_pipeline.json"
  "01_master_pipeline.json"
)

echo "=== Starting Workflows Deployment ==="

# Copy and Import each workflow
for file in "${FILES[@]}"; do
  echo "--> Processing workflow: $file..."
  
  # Copy file to container's /tmp directory
  docker cp "$WORKFLOW_DIR/$file" "$N8N_CONTAINER:/tmp/$file"
  
  # Import workflow using n8n CLI
  docker exec -u node "$N8N_CONTAINER" n8n import:workflow --input="/tmp/$file"
  
  # Clean up /tmp file inside container
  docker exec -u node "$N8N_CONTAINER" rm "/tmp/$file"
done

echo "--> Restarting n8n container to register webhooks and load state..."
docker restart "$N8N_CONTAINER"

echo "=== Deployment Completed Successfully! ==="
