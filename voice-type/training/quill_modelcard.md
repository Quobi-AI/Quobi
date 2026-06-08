---
license: apache-2.0
language:
- en
base_model:
- Qwen/Qwen3.5-0.8B
- Qwen/Qwen3.5-2B
- Qwen/Qwen3.5-4B
library_name: gguf
tags:
- dictation
- speech-to-text
- text-cleanup
- post-asr
- qwen3.5
- gguf
- llama.cpp
- on-device
pipeline_tag: text-generation
inference: false
---

# Quill — on-device dictation cleanup models

**Quill** is a family of small language models that turn raw speech-to-text
output into clean, written text — **entirely on your own device**. It removes
filler words (*um*, *uh*, *like*, *you know*), fixes punctuation and
capitalization, repairs spoken self-corrections and false starts, and collapses
the stutters and repeats that dictation produces — without changing your words
or sending anything to the cloud.

Quill is the cleanup stage of **[Quobi](https://huggingface.co/quobi)**, a
private, offline dictation app for desktop and mobile.

## What this is

When you dictate, a speech recognizer (e.g. Whisper) produces a literal, messy
transcript:

> *"um so i was thinking like maybe we could you know meet up at three"*

Quill rewrites that into what you actually meant to write:

> **"So I was thinking maybe we could meet up at three."**

It is **not** a chatbot and not an instruction-following assistant — it does one
job: clean dictated text. Feeding it questions or commands will not get answers;
it will just clean the text.

## Base model & credit

Quill is a fine-tune of **[Qwen3.5](https://huggingface.co/Qwen)** by the Qwen
team (Alibaba), used under the **Apache 2.0** license. Qwen3.5 is a hybrid
architecture interleaving **Mamba-2 / state-space (SSM)** layers with periodic
full-attention layers, which makes the small sizes fast and memory-light —
well suited to on-device, low-latency cleanup. All credit for the base models
goes to the Qwen team; Quill only adds task-specific fine-tuning.

| Quill tier | Base model | Size (Q4_K_M) |
|---|---|---|
| `quill-0.8b-Q4_K_M.gguf` | [Qwen/Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) | 505 MB |
| `quill-2b-Q4_K_M.gguf` | [Qwen/Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B) | 1.2 GB |
| `quill-4b-Q4_K_M.gguf` | [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) | 2.6 GB |

## Which tier to use

| Tier | Best for | Behavior |
|---|---|---|
| **0.8B** | Phones and any CPU (recommended default) | **Verbatim** — faithful cleanup, no rephrasing |
| **2B** | Mid-range machines / a modest GPU | Verbatim + light tidying |
| **4B** | Desktops with a GPU | Verbatim + tidying + light formatting |

The smaller tiers are deliberately conservative. The **0.8B is verbatim-only by
design**: it is paired with a deterministic post-processing scaffold (symbol,
email, URL, and number normalization) so the model never has to *guess* at
conversions like "at" → `@`. This keeps the tiny model accurate and predictable;
the larger tiers take on more rewriting and structure.

## Usage (llama.cpp)

```bash
llama-server -m quill-0.8b-Q4_K_M.gguf --host 127.0.0.1 --port 8080 -ngl 99
```

**Prompt format — important.** Use ChatML with the assistant turn pre-seeded
with an **empty think block** so the model does not emit chain-of-thought:

```
<|im_start|>system
You clean up dictated text.<|im_end|>
<|im_start|>user
yeah so um the meeting is gonna be like at uh three thirty tomorrow i think<|im_end|>
<|im_start|>assistant
<think>

</think>

```

→ **"The meeting is at 3:30 tomorrow."**

> ⚠️ Do **not** pass `--jinja` — it re-enables chain-of-thought leakage. Use the
> raw prompt above (or the `/completion` endpoint) with the pre-seeded empty
> `<think></think>` block. Greedy decoding (`temperature = 0`) is recommended.

## Intended use & limitations

- **Intended:** post-ASR cleanup of first-person English dictation.
- **Not intended:** as a general assistant, translator, or summarizer; for
  languages other than English (non-English text is passed through, not
  cleaned); for safety-critical rewriting.
- Like any LM it can occasionally over- or under-edit. The verbatim tiers
  minimize this by preserving your wording; pair them with the deterministic
  scaffold for symbol/number normalization.

## License

**Apache 2.0**, inherited from the Qwen3.5 base models (also Apache 2.0). You
are free to use, modify, and redistribute, including commercially, under the
terms of the license. Fine-tuned and released as part of the **Quobi** project.
