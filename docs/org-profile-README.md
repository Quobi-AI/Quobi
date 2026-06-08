<!--
  This is the Quobi organization profile README.
  It does NOT belong in the Quobi repo. To use it:
    1. Create a repo named ".github" under the Quobi-AI organization.
    2. Put this file at: profile/README.md
  The logo is pulled by raw URL from the public Quobi repo, so it shows up once
  that repo is public. If you would rather keep it self-contained, copy the two
  lockup PNGs into the .github repo and use relative paths instead.
-->

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Quobi-AI/Quobi/main/brand/exports/lockup/quobi-lockup-dark-192h.png">
    <img src="https://raw.githubusercontent.com/Quobi-AI/Quobi/main/brand/exports/lockup/quobi-lockup-192h.png" alt="Quobi" height="92">
  </picture>
</p>

<p align="center"><b>Private, on-device dictation.</b></p>

---

We build dictation that runs entirely on your own machine. You talk, and your words show up as clean text in whatever app you are using, with the filler and false starts removed and the punctuation fixed. Nothing is sent to a server. No accounts, no API keys, no per-use fees, and your audio never leaves your computer.

Most dictation tools either type out the raw mess or stream your voice to someone else's cloud. Quobi does neither. A speech model transcribes locally, then a small fine-tuned model called Quill rewrites the result into natural text. Both run on your GPU through Vulkan, so they work on any GPU with no CUDA to install, and fall back to the CPU when there is none.

### Projects

- **[Quobi](https://github.com/Quobi-AI/Quobi)** is the desktop app for Linux and Windows.
- **[Quill](https://huggingface.co/quobi/quill)** is the set of open cleanup models (Apache-2.0) on Hugging Face.

### Why on-device

Because your voice is yours. Keeping everything local means there is nothing to leak, nothing to hand over, and nothing to pay per minute. It keeps working with no internet, and the code is open so you can check that the promise holds.
