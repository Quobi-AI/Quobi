# Licensing

Quobi uses two different licenses for two different things. This page explains which is which and why.

## The application code: AGPL-3.0

Everything in this repository (the engine, the desktop app, the build tooling, the shared assets) is licensed under the **GNU Affero General Public License, version 3**. The full text is in [LICENSE](LICENSE).

In plain terms:

- You can use Quobi for anything, including at work and inside a business.
- You can read, modify, and redistribute the code.
- If you distribute a modified version, or run a modified version as a network service, you have to share your changes under the same AGPL-3.0 license.

We chose the AGPL on purpose. Quobi's whole promise is that your voice stays on your machine, and that promise is only believable if anyone can read the code and confirm it. Copyleft keeps the project, and any fork of it, open and auditable.

## The Quill models: Apache-2.0

The fine-tuned cleanup models (Quill) are **not** covered by the AGPL. They are distributed separately on Hugging Face at [quobi/quill](https://huggingface.co/quobi/quill) under the **Apache-2.0** license, so you are free to use them in research or in your own products, open or closed. They are fine-tuned from Qwen3.5; if you redistribute them, check the upstream base-model terms as well.

A note on what comes later: the base Quill models stay open under Apache-2.0. Specialized or higher-end models that Quobi trains down the line may be released under a separate proprietary license. If that happens, it will be stated clearly on the model itself, and it will not change the license of anything already published. If you would rather not use Quill at all, Quobi runs any compatible GGUF you provide.

## Commercial license

The AGPL does not work for everyone. If you want to ship Quobi inside a closed-source or proprietary product without the AGPL's share-your-changes obligation, a separate commercial license is available.

To start that conversation, open an issue with the `licensing` label. (Once there is a website and a contact address, this section will point there instead.)

## Contributions

Quobi uses a **Contributor License Agreement (CLA)**. Before your first contribution is merged, you sign the CLA in [CLA.md](CLA.md). It keeps your contribution under the project's AGPL-3.0 and also gives Quobi the right to offer the project under a commercial license, which is what makes the commercial option above possible. See [CONTRIBUTING.md](CONTRIBUTING.md) for how this works in practice.
