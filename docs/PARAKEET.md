# Parakeet STT: how the speech model is sourced

Quobi's speech-to-text engine is **NVIDIA Parakeet TDT 0.6B** (CC-BY-4.0), run
on-device through **sherpa-onnx** (ONNX Runtime, CPU): in-process inside the
daemon, no sidecar, no localhost port, no CUDA, and the same code path on Linux
and Windows. It is the only speech engine; there is no Whisper in the local path.

There are two model variants the GUI switches between (Settings -> Transcription
-> Language):

- **`english`** (default): `parakeet-tdt-0.6b-v2`, the best English model (HF
  Open ASR leaderboard #1 English). English only.
- **`multilingual`**: `parakeet-tdt-0.6b-v3`, 25 languages with automatic
  language detection (Bulgarian, Croatian, Czech, Danish, Dutch, English,
  Estonian, Finnish, French, German, Greek, Hungarian, Italian, Latvian,
  Lithuanian, Maltese, Polish, Portuguese, Romanian, Russian, Slovak, Slovenian,
  Spanish, Swedish, Ukrainian). Pick this only if you dictate in something other
  than English; for English the v2 model is more accurate.

There is no build or export step. k2-fsa publishes a prebuilt sherpa-onnx ONNX
bundle for each, and the daemon downloads the selected one on first run, SHA-256
verified, into `<models>/parakeet/<variant>/`.

## The bundles

Sources (pinned in `PARAKEET_VARIANTS` in `voice-type/voice_type/download.py`):

```
english       sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2
              sha256 157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad  (482,468,385 bytes)
multilingual  sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2
              sha256 5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf  (487,170,055 bytes)
```

both under `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/`.

`download_parakeet_model(variant)` fetches the tarball, verifies the SHA-256, and
extracts these four files (by basename, dropping the archive's `test_wavs/`) into
`<models>/parakeet/<variant>/`:

| File | Size |
| --- | --- |
| `encoder.int8.onnx` | ~620 MB |
| `decoder.int8.onnx` | ~7 MB |
| `joiner.int8.onnx` | ~1.7 MB |
| `tokens.txt` | ~10 KB |

The loader (`voice_type/transcribe_parakeet.py`) reads them with
`OfflineRecognizer.from_transducer(..., model_type="nemo_transducer")` (sherpa-onnx
loads TDT and RNN-T the same way).

The model is CC-BY-4.0 (NVIDIA). Keep NVIDIA's attribution if you redistribute.

## Why CPU, and why no Whisper

Parakeet on the CPU is benchmarked at ~23x faster than real-time on a single core
(RTF 0.044 on a 7.4s clip) and 50x+ multi-threaded, so speech never needs the GPU
on any hardware regardless of GPU vendor. A CPU is a CPU: an AMD or Intel machine
runs Parakeet exactly like an NVIDIA or GPU-less one. That leaves the GPU entirely
for the Quill cleanup model. Between the English (v2) and multilingual (v3)
Parakeet variants, every language is covered, so there is no remaining reason to
ship Whisper and the local path is Parakeet only. (The optional `engine = "cloud"`
path still exists for users who bring their own OpenAI-compatible Whisper API key,
but that is a separate, off-by-default feature.)

## Adding or changing a variant

To add or swap a model, edit the `PARAKEET_VARIANTS` table in `download.py` (each
entry is `model_id` / `url` / `sha256` / `bytes`), pointing at another prebuilt
sherpa-onnx bundle or one you host. Keep the four-file layout the loader expects.
If a model ships only as a NeMo checkpoint, export it first with the scripts under
`sherpa-onnx/scripts/nemo/`.
