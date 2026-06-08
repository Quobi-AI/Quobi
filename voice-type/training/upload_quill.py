#!/usr/bin/env python3
"""Upload the Quill cleanup GGUFs + model card to quobi/quill on HuggingFace.

Run AFTER `hf auth login` (needs a write token for the `quobi` org):

    python3 upload_quill.py            # upload everything
    python3 upload_quill.py --card     # upload just the README
    python3 upload_quill.py --dry-run  # show what would happen

Each GGUF is uploaded under its branded repo name. SHA-256s below are the
verified hashes of the local source files (also used by the app's download
verifier).
"""
from __future__ import annotations

import sys
from pathlib import Path

from huggingface_hub import HfApi

REPO = "quobi/quill"
HOME = Path.home()

# (local source, repo filename, sha256)
UPLOADS = [
    (
        HOME / "v42-eval/qwen35-0.8b-v42-bundle/qwen35-0.8b-cleanup-Q4_K_M.gguf",
        "quill-0.8b-Q4_K_M.gguf",
        "aa54d6f6108d66e4b60a57bdc04ecca6e84e073504918a64b41ac4a0f816f16d",
    ),
    (
        HOME / "v4-models/qwen35-2b-v4-bundle/qwen35-2b-cleanup-Q4_K_M.gguf",
        "quill-2b-Q4_K_M.gguf",
        "b877a22b773d2aac40b3c642c24f1cbbb0b3f1d42cbd3c6eb936533719317196",
    ),
    (
        HOME / ".local/share/voice-type/models/quill-4b-Q4_K_M.gguf",
        "quill-4b-Q4_K_M.gguf",
        "e5e6bd7e92690c6f954399c473e740561d9deff0862e1bfe42c1f6055535b987",
    ),
]
CARD = Path(__file__).with_name("quill_modelcard.md")


def main() -> int:
    args = set(sys.argv[1:])
    dry = "--dry-run" in args
    card_only = "--card" in args
    api = HfApi()

    who = api.whoami()
    print("authed as:", who.get("name"), "| orgs:", [o.get("name") for o in who.get("orgs", [])])

    # README first
    if CARD.exists():
        print(f"[card] {CARD.name} -> {REPO}/README.md")
        if not dry:
            api.upload_file(path_or_fileobj=str(CARD), path_in_repo="README.md",
                            repo_id=REPO, repo_type="model",
                            commit_message="Add Quill model card")
    if card_only:
        return 0

    for src, name, _sha in UPLOADS:
        if not src.exists():
            print(f"[skip] missing: {src}")
            continue
        size_mb = src.stat().st_size / 1e6
        print(f"[gguf] {src.name} ({size_mb:.0f} MB) -> {REPO}/{name}")
        if not dry:
            api.upload_file(path_or_fileobj=str(src), path_in_repo=name,
                            repo_id=REPO, repo_type="model",
                            commit_message=f"Add {name}")
    print("done." if not dry else "dry run — nothing uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
