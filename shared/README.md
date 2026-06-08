# shared/

Cross-platform assets used by both the Linux desktop daemon (`voice-type/`)
and the Android keyboard app (`voice-type-android/`).

The point of this directory is **single-source-of-truth** for the things
that have to behave identically on both platforms:

| File | What it is | Edit it when |
| --- | --- | --- |
| `cleanup-prompt.txt` | The Llama cleanup system prompt — the project's "moat" | You want to change how the LLM cleans transcripts |
| `scratch-phrases.json` | List of voice commands that erase the previous paste | Adding a new "undo" synonym |
| `hallucinations.json` | Whisper outputs to filter on short/silent clips | A new Whisper artifact starts showing up |

Both apps load these at startup. Touch a file here, rebuild either app,
behavior changes everywhere.

## How each platform consumes this directory

**Linux (`voice-type/`)** — `voice_type._shared` resolves the path at runtime:
- dev mode: `../../shared/` relative to the package
- PyInstaller binary: bundled into `sys._MEIPASS/shared/` via `voice-type.spec`'s `datas`

**Android (`voice-type-android/`)** — gradle exposes this dir as an
`assets` source set (see the Android app's `build.gradle.kts`). Files are
read via `context.assets.open("shared/<name>")`.
