#!/usr/bin/env python3
"""Full fine-tune Qwen3.5-2B on the dictation-cleanup dataset.

Runs on the 96 GB GPU box (NOT in the daemon venv — see requirements-train.txt).

Key design choices, all deliberate:
  * Plain transformers.Trainer (the engine inside TRL's SFTTrainer) — it has
    ZERO architecture-specific logic, so it's the safest path for the brand-new
    `qwen3_5` hybrid arch. We do the chat-template + loss-masking ourselves so
    nothing is hidden.
  * ASSISTANT-ONLY LOSS MASKING: we only compute loss on the cleaned-text
    completion, never on the system prompt / user transcript. This is the single
    most important thing for a clean cleanup model — it learns to *produce* the
    edit, not to memorize the prompt.
  * Full fine-tune in bf16 (96 GB allows it; ~36-45 GB for a 2B). No LoRA.
  * The model is multimodal (Qwen3_5ForConditionalGeneration); we train text
    only — the vision tower simply never sees an image and stays idle.

Usage:
  python train_qwen35.py \
      --model models/Qwen3.5-2B \
      --data data/verbatim_all.jsonl \
      --out out/qwen35-2b-verbatim \
      --epochs 3
"""
from __future__ import annotations

import argparse
import json

import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    Trainer,
    TrainingArguments,
)


def load_model(path: str):
    """Load for text-only causal LM training. qwen3_5 is a multimodal
    ConditionalGeneration model; AutoModelForCausalLM usually resolves to its
    text path. If your transformers version refuses, swap to
    AutoModelForImageTextToText (it has the same LM head + loss) — both train
    the text decoder identically when fed text-only input_ids + labels."""
    kw = dict(torch_dtype=torch.bfloat16, trust_remote_code=True)
    try:
        return AutoModelForCausalLM.from_pretrained(path, **kw)
    except (ValueError, KeyError) as e:
        print(f"AutoModelForCausalLM failed ({e}); trying ImageTextToText...")
        from transformers import AutoModelForImageTextToText
        return AutoModelForImageTextToText.from_pretrained(path, **kw)


def build_dataset(data_path: str, tok, max_len: int):
    raw = load_dataset("json", data_files=data_path, split="train")

    def tokenize(example):
        msgs = example["messages"]
        # Prompt = everything up to (and including) the generation prompt.
        prompt_ids = tok.apply_chat_template(
            msgs[:-1], add_generation_prompt=True, tokenize=True,
        )
        # Full = prompt + the assistant's cleaned text.
        full_ids = tok.apply_chat_template(
            msgs, add_generation_prompt=False, tokenize=True,
        )
        full_ids = full_ids[:max_len]
        labels = list(full_ids)
        # Mask the prompt span — loss only on the assistant completion.
        for i in range(min(len(prompt_ids), len(labels))):
            labels[i] = -100
        return {"input_ids": full_ids, "labels": labels,
                "attention_mask": [1] * len(full_ids)}

    cols = raw.column_names
    return raw.map(tokenize, remove_columns=cols, desc="tokenize+mask")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/Qwen3.5-2B")
    ap.add_argument("--data", default="data/verbatim_all.jsonl")
    ap.add_argument("--out", default="out/qwen35-2b-verbatim")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=1e-5)  # full-FT: keep it low
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--bs", type=int, default=8, help="per-device batch size")
    ap.add_argument("--grad-accum", type=int, default=2)
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token

    ds = build_dataset(args.data, tok, args.max_len)
    print(f"dataset: {len(ds)} examples")

    model = load_model(args.model)
    model.config.use_cache = False          # required with gradient checkpointing
    model.gradient_checkpointing_enable()

    targs = TrainingArguments(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.bs,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        bf16=True,
        logging_steps=5,
        save_strategy="epoch",
        report_to="none",
        gradient_checkpointing=True,
    )

    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=ds,
        data_collator=DataCollatorForSeq2Seq(tok, padding=True, label_pad_token_id=-100),
    )
    trainer.train()
    trainer.save_model(args.out)
    tok.save_pretrained(args.out)
    print(f"\nsaved fine-tuned model -> {args.out}")
    print("next: export_gguf.sh to convert + quantize for on-device.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
