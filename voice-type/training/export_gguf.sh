#!/usr/bin/env bash
# Convert a fine-tuned Qwen3.5-2B checkpoint -> GGUF -> Q4_K_M, for on-device
# (llama.cpp / Android / desktop). qwen3_5 support landed in llama.cpp b9180
# (MTP) and is stable by b9222 — we pin to a recent tag.
#
# The GGUF converter extracts ONLY the language model, so the vision tower is
# dropped automatically. Output is a ~1.5 GB text model.
#
# Usage:  ./export_gguf.sh out/qwen35-2b-verbatim
set -euo pipefail

MODEL_DIR="${1:?usage: export_gguf.sh <fine-tuned-model-dir>}"
LLAMA_TAG="${LLAMA_TAG:-b9222}"          # >= b9222 for qwen3_5
WORK="${WORK:-$HOME/llama.cpp}"
OUT="${MODEL_DIR%/}-gguf"
mkdir -p "$OUT"

# 1. Get + build llama.cpp at a qwen3_5-capable tag.
if [ ! -d "$WORK" ]; then
  git clone https://github.com/ggml-org/llama.cpp "$WORK"
fi
git -C "$WORK" fetch --tags --quiet
git -C "$WORK" checkout "$LLAMA_TAG"
cmake -S "$WORK" -B "$WORK/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$WORK/build" -j --target llama-quantize llama-cli >/dev/null
pip install -q -r "$WORK/requirements.txt"

# 2. HF checkpoint -> full-precision GGUF (vision auto-stripped).
F16="$OUT/model-f16.gguf"
python "$WORK/convert_hf_to_gguf.py" "$MODEL_DIR" --outfile "$F16" --outtype f16

# 3. Quantize to Q4_K_M (~1.5 GB) — the on-device shipping quant.
Q4="$OUT/qwen35-2b-cleanup-Q4_K_M.gguf"
"$WORK/build/bin/llama-quantize" "$F16" "$Q4" Q4_K_M

echo
echo "done:"
echo "  full:  $F16"
echo "  quant: $Q4   <- ship this on device"
echo
echo "smoke test:"
echo "  $WORK/build/bin/llama-cli -m $Q4 -p 'clean this' -n 20"
