# Parakeet STT: how the speech model is sourced

Quobi's speech-to-text engine is **NVIDIA Parakeet TDT 0.6B v3**: multilingual
(25 languages with automatic language detection), CC-BY-4.0. It runs on-device
through **sherpa-onnx** (ONNX Runtime, CPU): in-process inside the daemon, no
sidecar, no localhost port, no CUDA, and the same code path on Linux and Windows.
It is the only speech engine; there is no Whisper in the local path.

There is no build or export step. k2-fsa publishes a prebuilt sherpa-onnx ONNX
bundle, and the daemon downloads it on first run, SHA-256 verified.

## Languages

Bulgarian, Croatian, Czech, Danish, Dutch, English, Estonian, Finnish, French,
German, Greek, Hungarian, Italian, Latvian, Lithuanian, Maltese, Polish,
Portuguese, Romanian, Russian, Slovak, Slovenian, Spanish, Swedish, Ukrainian.
The model detects the spoken language automatically, no setting required.

## The bundle

Source (pinned in `voice-type/voice_type/download.py`):

```
https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2
sha256 5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf  (487,170,055 bytes)
```

`download_parakeet_model()` fetches the tarball, verifies the SHA-256, and
extracts these four files (by basename, dropping the archive's `test_wavs/`) into
`<models>/parakeet/`:

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
for the Quill cleanup model. Because v3 is multilingual, there is no remaining
reason to ship Whisper, so the local path is Parakeet only. (The optional
`engine = "cloud"` path still exists for users who bring their own OpenAI-
compatible Whisper API key, but that is a separate, off-by-default feature.)

## Swapping the model

To use a different Parakeet variant, point `PARAKEET_URL` / `PARAKEET_SHA256` /
`PARAKEET_BYTES` in `download.py` at another prebuilt sherpa-onnx bundle, or host
your own. Keep the four-file layout the loader expects. If a variant ships only as
a NeMo checkpoint, export it first with the scripts under
`sherpa-onnx/scripts/nemo/`.
