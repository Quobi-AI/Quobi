# Parakeet STT: how the speech model is sourced

Quobi's default speech-to-text engine on NVIDIA and CPU machines is **NVIDIA
Parakeet TDT 0.6B v2** (English), currently the #1 English model on the Hugging
Face Open ASR leaderboard. It runs on-device through **sherpa-onnx** (ONNX
Runtime, CPU): in-process inside the daemon, no sidecar, no localhost port, no
CUDA, and the same code path on Linux and Windows.

There is no build or export step. k2-fsa publishes a prebuilt sherpa-onnx ONNX
bundle, and the daemon downloads it on first run, SHA-256 verified.

## The bundle

Source (pinned in `voice-type/voice_type/download.py`):

```
https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2
sha256 157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad  (482,468,385 bytes)
```

`download_parakeet_model()` fetches the tarball, verifies the SHA-256, and
extracts these four files (by basename, dropping the archive's `test_wavs/`) into
`<models>/parakeet/`:

| File | Size |
| --- | --- |
| `encoder.int8.onnx` | ~622 MB |
| `decoder.int8.onnx` | ~7 MB |
| `joiner.int8.onnx` | ~1.7 MB |
| `tokens.txt` | ~9 KB |

The loader (`voice_type/transcribe_parakeet.py`) reads them with
`OfflineRecognizer.from_transducer(..., model_type="nemo_transducer")`.

The model is CC-BY-4.0 (NVIDIA). Keep NVIDIA's attribution if you redistribute.

## STT engine gating

`[transcribe].stt` selects the engine:

- `auto` (default): Parakeet on NVIDIA / no GPU; **whisper.cpp Vulkan on AMD
  GPUs**. The reason for the AMD carve-out is that Parakeet runs on ONNX Runtime,
  which has no Vulkan execution provider and no clean cross-platform AMD-GPU path
  (DirectML is Windows-only and needs a source build; ROCm/MIGraphX is Linux-only
  and being removed from ORT). whisper.cpp Vulkan GPU-accelerates on any GPU,
  including AMD, so AMD users get real GPU acceleration there.
- `parakeet`: force Parakeet (sherpa-onnx, CPU).
- `whisper`: force whisper.cpp Vulkan.

The GUI sets this from the **Speech engine** control in Settings and detects the
machine's GPU vendor (`recommended_stt`) to pick what `auto` resolves to.

## Swapping the model

To use a different Parakeet variant (e.g. a future v3, or the 1.1B models), point
`PARAKEET_URL` / `PARAKEET_SHA256` / `PARAKEET_BYTES` in `download.py` at another
prebuilt sherpa-onnx bundle, or host your own. Keep the four-file layout the
loader expects. If a variant ships only as a NeMo checkpoint, export it first
with the scripts under `sherpa-onnx/scripts/nemo/`.
