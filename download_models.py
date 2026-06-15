"""
Download InsightFace buffalo_sc model with resume support.

Run once before main.py — safe to interrupt and re-run, picks up where it left off.

    python download_models.py

The model (~30 MB zip) is saved to ~/.insightface/models/buffalo_sc/
"""

import os
import sys
import zipfile

import requests

MODEL_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip"
MODELS_DIR = os.path.expanduser("~/.insightface/models")
ZIP_PATH   = os.path.join(MODELS_DIR, "buffalo_sc.zip")
DEST_DIR   = os.path.join(MODELS_DIR, "buffalo_sc")
CHUNK      = 1024 * 1024  # 1 MB


def download_with_resume(url: str, dest: str) -> bool:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    existing = os.path.getsize(dest) if os.path.exists(dest) else 0

    headers = {"Range": f"bytes={existing}-"} if existing else {}
    if existing:
        print(f"  Resuming from {existing / 1024 / 1024:.1f} MB already downloaded.")

    try:
        r = requests.get(url, headers=headers, stream=True, timeout=30, allow_redirects=True)
    except requests.exceptions.Timeout:
        print("  Connection timed out.")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"  Connection error: {e}")
        return False

    if r.status_code == 416:
        print("  File already fully downloaded.")
        return True

    if r.status_code not in (200, 206):
        print(f"  Server returned HTTP {r.status_code}")
        return False

    # Total size from Content-Range (resume) or Content-Length (fresh)
    cr = r.headers.get("Content-Range", "")
    total = int(cr.split("/")[-1]) if cr else int(r.headers.get("Content-Length", 0))
    downloaded = existing

    mode = "ab" if existing else "wb"
    try:
        with open(dest, mode) as f:
            for chunk in r.iter_content(chunk_size=CHUNK):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        bar = "#" * int(pct / 2)
                        sys.stdout.write(
                            f"\r  [{bar:<50}] {pct:5.1f}%  "
                            f"{downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB"
                        )
                        sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n\n  Paused. Run again to resume from this point.")
        return False

    print()
    return True


def verify_zip(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                print(f"  Zip corrupted at: {bad}")
                return False
        return True
    except zipfile.BadZipFile:
        print("  File is not a valid zip — download may be incomplete.")
        return False


def extract(zip_path: str, dest_dir: str) -> None:
    print(f"  Extracting to {dest_dir} ...")
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(dest_dir)


def main():
    print("=== InsightFace model download ===")
    print(f"Model : buffalo_sc")
    print(f"Target: {DEST_DIR}\n")

    # Already extracted?
    if os.path.isdir(DEST_DIR) and os.listdir(DEST_DIR):
        print("Model already present. Nothing to do.")
        print("Run: python main.py")
        return

    print("Downloading...")
    ok = download_with_resume(MODEL_URL, ZIP_PATH)
    if not ok:
        remaining = ""
        if os.path.exists(ZIP_PATH):
            done = os.path.getsize(ZIP_PATH)
            remaining = f" ({done/1024/1024:.1f} MB saved — run again to continue)"
        print(f"Download incomplete.{remaining}")
        sys.exit(1)

    print("\nVerifying zip integrity...")
    if not verify_zip(ZIP_PATH):
        print("Deleting corrupt file — run again to restart download.")
        os.remove(ZIP_PATH)
        sys.exit(1)

    extract(ZIP_PATH, DEST_DIR)
    os.remove(ZIP_PATH)

    print("\nModel ready.")
    print("Run: python main.py")


if __name__ == "__main__":
    main()
