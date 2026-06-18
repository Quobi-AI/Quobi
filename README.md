<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/exports/lockup/quobi-lockup-dark-192h.png">
    <img src="brand/exports/lockup/quobi-lockup-192h.png" alt="Quobi" height="84">
  </picture>
</p>

<p align="center">
  <b>Private, on-device dictation that cleans up after you.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg" alt="License: AGPL-3.0">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20Windows-555" alt="Platform: Linux | Windows">
  <img src="https://img.shields.io/badge/cloud-none-success" alt="Cloud: none">
</p>

---

Quobi turns speech into finished text without sending anything to a server. You hold a hotkey, talk, and let go. Your words land in whatever app you are using, already stripped of the ums, false starts, and repeated words, with punctuation and capitalization sorted out. No API keys, no per-minute fees, and your audio never leaves your machine.

Most dictation tools pick one of two bad options. They either type out exactly what you said and leave you to clean up the mess, or they ship your voice off to someone else's cloud. Quobi does neither. It runs two local models back to back: NVIDIA's Parakeet speech model turns your speech into text on the CPU, then a small fine-tuned language model called **Quill** rewrites that into clean, natural prose on the GPU.

You decide how much it edits:

- **Verbatim** keeps your exact words. It only removes filler and fixes punctuation and capitalization.
- **Tidy** fixes grammar, merges fragments, and repairs run-on sentences while keeping your voice and meaning.
- **Formatted** does everything Tidy does and adds bullet lists and paragraph breaks when you actually dictate them.

## How it works

```
  hotkey  >  record  >  Parakeet speech-to-text  >  Quill cleanup  >  paste into your app
                        sherpa-onnx, CPU             llama.cpp, Vulkan/CPU
```

Speech-to-text runs NVIDIA Parakeet in-process through ONNX Runtime on the CPU. It handles 25 languages with automatic language detection, and it is fast: over 20 times quicker than real time even on a single core, so it needs no GPU at all and runs the same on any machine whether the GPU is AMD, Intel, NVIDIA, or absent. That leaves the GPU entirely for the Quill cleanup model, which runs through **Vulkan** on any GPU with no CUDA to install and falls back to the CPU when there is none. On a typical machine transcription is well under a second and cleanup adds about another second, so there is almost no wait between letting go of the hotkey and the text landing.

Everything is local. There is no account, no telemetry, and no network call in the dictation path. If you want to confirm that, the code is right here.

## Install

Grab the latest build for your OS from the [Releases](../../releases) page:

- **Linux** ships as an AppImage. Make it executable and run it:
  ```bash
  chmod +x Quobi*.AppImage
  ./Quobi*.AppImage
  ```
- **Windows** ships as an installer. Run it and launch Quobi from the Start menu.

On first launch, open Settings and download a speech model and a cleanup model. The cleanup models (Quill) come in three sizes so you can trade speed for quality on your hardware.

## Build from source

The full build, for both Linux and Windows, is documented in [BUILD.md](BUILD.md). The short version on Linux:

```bash
# build the dictation engine (the Python daemon)
cd voice-type && make build && cd ..

# build the desktop app + AppImage (bundles the engine and the Vulkan cleanup sidecar)
cd voice-type-desktop && NO_STRIP=1 bun run tauri build
```

You will need Rust, Bun, Python 3, and a few system libraries listed in BUILD.md.

## The models

The cleanup is done by **Quill**, a set of models we fine-tune in-house (from Qwen3.5, in 0.8B, 2B, and 4B sizes). They are open and live on Hugging Face at [quobi/quill](https://huggingface.co/quobi/quill) under **Apache-2.0**, so you can inspect them, run them anywhere, or bring your own GGUF instead. Quobi checks a model's SHA-256 before it loads. The speech side uses NVIDIA's open **Parakeet** TDT 0.6B v3 model (CC-BY-4.0, 25 languages with automatic language detection) run through sherpa-onnx on the CPU.

Note: the base Quill models stay open. Specialized or higher-end models we train later may ship under a separate license, but that will not change anything already published.

## Privacy

This is the whole point, so it is worth stating plainly:

- Your audio is recorded, transcribed, and cleaned up entirely on your own device.
- Nothing in the dictation path touches the network.
- There is no account and no telemetry.
- The models you download are fetched over HTTPS and checked against a known SHA-256 before they are ever loaded.

## Repository layout

| Path | What it is |
| --- | --- |
| `voice-type/` | the dictation engine (Python daemon: hotkey, audio, transcription, cleanup) |
| `voice-type-desktop/` | the desktop app (Tauri: Rust core + React UI) |
| `shared/` | prompt and config assets shared across platforms |
| `brand/` | logo, wordmark, and icon assets |
| `BUILD.md` | the full build and release pipeline |

## License

The Quobi application code in this repository is licensed under the **GNU AGPL-3.0** (see [LICENSE](LICENSE)). The **Quill cleanup models are Apache-2.0**, distributed on Hugging Face. Contributions are accepted under a Contributor License Agreement (see [CONTRIBUTING.md](CONTRIBUTING.md)). If the AGPL does not fit your use, for example you want to embed Quobi in a closed-source product, a commercial license is available. The full picture is in [LICENSING.md](LICENSING.md).

## Roadmap

- Android (the on-device core already exists and is in progress; desktop ships first)
- A wider catalog of downloadable cleanup and speech models
- Per-app dictation profiles

---

<p align="center"><sub>Quobi. Your voice, your machine, your text.</sub></p>
