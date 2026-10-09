#!/bin/bash
# Dipendenze sistema per gcp_diag — solo tkinter, tutto il resto è stdlib Python.
# NON usare pip: Ubuntu 24.04+ blocca pip fuori da venv (PEP 668).
set -e

echo "=== Installazione dipendenze sistema ==="
sudo apt install -y python3-tk

echo ""
echo "=== Verifica strumenti GCP (opzionali ma necessari per le funzionalità) ==="
for tool in gcloud kubectl gke-gcloud-auth-plugin; do
    if command -v "$tool" &>/dev/null; then
        echo "  ✓ $tool: $(command -v $tool)"
    else
        echo "  ✗ $tool: non trovato"
    fi
done

echo ""
echo "Pronto. Lancia con: python3 gui.py"
