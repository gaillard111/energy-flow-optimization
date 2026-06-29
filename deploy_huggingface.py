#!/usr/bin/env python3
"""
deploy_huggingface.py — Deploiement du corpus sur Hugging Face Datasets.

Cree le dataset 'mttv-energy-flow-optimization' et y depose tous les fichiers
du corpus (Markdown + code Python).

Usage :
    python deploy_huggingface.py

Prerequis :
    huggingface_hub >= 0.20.0
    Hugging Face token configure (hf_xxx)
"""

import os
import sys
from pathlib import Path
from huggingface_hub import (
    HfApi,
    create_repo,
    upload_file,
    DatasetCard,
)

# Configuration
CORPUS_DIR = Path(__file__).parent
DATASET_ID = "mttv-energy-flow-optimization"
REPO_ID = f"girard444/{DATASET_ID}"
REPO_TYPE = "dataset"

FILES_TO_UPLOAD = [
    "README.md",
    "optimisation_flux_energetiques.md",
    "optimisation_polyfocale.py",
    "arxiv_preprint.md",
]


def main():
    print(f"=== Deploiement sur Hugging Face : {REPO_ID} ===\n")

    # Verification de l'authentification
    api = HfApi()
    try:
        user_info = api.whoami()
        print(f"  Connecte en tant que : {user_info['name']}")
    except Exception as e:
        print(f"  ERREUR : Non connecte a Hugging Face. {e}")
        print("  Execute : huggingface-cli login")
        sys.exit(1)

    # 1. Creation du dataset
    print(f"\n  [1/4] Creation du dataset '{REPO_ID}'...")
    try:
        url = create_repo(
            repo_id=REPO_ID,
            repo_type=REPO_TYPE,
            private=False,
            exist_ok=True,
        )
        print(f"    [OK] Dataset cree : {url}")
    except Exception as e:
        print(f"    ERREUR lors de la creation : {e}")
        sys.exit(1)

    # 2. Upload des fichiers
    print(f"\n  [2/4] Upload des fichiers du corpus...")
    for filename in FILES_TO_UPLOAD:
        filepath = CORPUS_DIR / filename
        if not filepath.exists():
            print(f"    [!] Fichier introuvable : {filename}")
            continue

        print(f"    Upload : {filename}...", end=" ")
        try:
            upload_file(
                path_or_fileobj=str(filepath),
                path_in_repo=filename,
                repo_id=REPO_ID,
                repo_type=REPO_TYPE,
            )
            print("[OK]")
        except Exception as e:
            print(f"ERREUR : {e}")

    # 3. Verification du deploiement
    print(f"\n  [3/4] Verification du deploiement...")

    # 4. Fin
    print(f"\n  [4/4] Fin du deploiement...")
    try:
        files = api.list_repo_files(repo_id=REPO_ID, repo_type=REPO_TYPE)
        print(f"    Fichiers presents sur Hugging Face :")
        for f in files:
            print(f"      - {f}")
        print(f"\n    [OK] Deploiement termine avec succes !")
        print(f"    -> https://huggingface.co/datasets/{REPO_ID}")
    except Exception as e:
        print(f"    ERREUR lors de la verification : {e}")

    print("\n=== Fin du deploiement ===")


if __name__ == "__main__":
    main()
