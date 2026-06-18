# Parakeet STT: export and host (one-time)

Quobi's default speech-to-text engine is **NVIDIA Parakeet RNN-T 1.1B**
(English), run on-device through **sherpa-onnx** (ONNX Runtime, CPU). It runs
in-process inside the daemon: no sidecar, no localhost port, no CUDA, and the
same code path on Linux and Windows.

Parakeet ships from NVIDIA as a NeMo checkpoint, not as ONNX. So there is a
one-time job: export the checkpoint to the sherpa-onnx ONNX layout, quantize it,
upload it to Hugging Face, and pin the SHA-256s in the daemon. End users never do
this; they just download the finished bundle on first run.

You run this once, on the GPU box (the 96 GB machine). The output is four files
that get hosted at `https://huggingface.co/quobi/parakeet-rnnt-1.1b-onnx`.

## The bundle the daemon expects

All four land in `<models>/parakeet/` on the user's machine:

| File | What it is |
| --- | --- |
| `encoder.int8.onnx` | FastConformer encoder, int8-quantized (the big one) |
| `decoder.int8.onnx` | RNN-T prediction network |
| `joiner.int8.onnx` | RNN-T joint network |
| `tokens.txt` | the BPE vocabulary |

The loader (`voice_type/transcribe_parakeet.py`) prefers the `.int8.onnx`
variants and falls back to unquantized `.onnx` if a file is not quantized.

## 1. Environment (GPU box)

```bash
python -m venv .nemo && . .nemo/bin/activate
pip install -U "nemo_toolkit[asr]" onnx onnxruntime
git clone https://github.com/k2-fsa/sherpa-onnx
```

## 2. Export NeMo -> ONNX

sherpa-onnx ships the export scripts. Use the NeMo transducer exporter under
`sherpa-onnx/scripts/nemo/` (the same family of scripts documented at
https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/nemo-transducer-models.html).
Point it at the pretrained model id:

```bash
cd sherpa-onnx/scripts/nemo
# downloads nvidia/parakeet-rnnt-1.1b and writes encoder/decoder/joiner .onnx
# plus tokens.txt into the current dir
python ./export-onnx-transducer.py --model nvidia/parakeet-rnnt-1.1b
```

If NeMo emits a combined `decoder_joint` graph, the sherpa-onnx script splits it
into the separate `decoder.onnx` + `joiner.onnx` that sherpa-onnx loads. Confirm
you end up with `encoder.onnx`, `decoder.onnx`, `joiner.onnx`, `tokens.txt`.

## 3. Quantize to int8

```bash
python -m onnxruntime.quantization.preprocess --input encoder.onnx --output encoder.pre.onnx
python - <<'PY'
from onnxruntime.quantization import quantize_dynamic, QuantType
quantize_dynamic("encoder.pre.onnx", "encoder.int8.onnx", weight_type=QuantType.QInt8)
PY
# decoder/joiner are tiny; ship them unquantized (the loader accepts plain .onnx)
```

## 4. Sanity-check locally before uploading

```bash
pip install sherpa-onnx soundfile
python - <<'PY'
import sherpa_onnx, soundfile as sf
rec = sherpa_onnx.OfflineRecognizer.from_transducer(
    encoder="encoder.int8.onnx", decoder="decoder.onnx", joiner="joiner.onnx",
    tokens="tokens.txt", num_threads=4, sample_rate=16000, feature_dim=80,
    decoding_method="greedy_search", model_type="nemo_transducer")
samples, sr = sf.read("test-16k-mono.wav", dtype="float32")
s = rec.create_stream(); s.accept_waveform(sr, samples); rec.decode_stream(s)
print(repr(s.result.text))
PY
```

This is the exact call `ParakeetTranscriber` makes, so if it transcribes here it
will work in the daemon.

## 5. Upload to Hugging Face

```bash
huggingface-cli upload quobi/parakeet-rnnt-1.1b-onnx encoder.int8.onnx
huggingface-cli upload quobi/parakeet-rnnt-1.1b-onnx decoder.int8.onnx
huggingface-cli upload quobi/parakeet-rnnt-1.1b-onnx joiner.int8.onnx
huggingface-cli upload quobi/parakeet-rnnt-1.1b-onnx tokens.txt
```

Name the decoder/joiner with `.int8.onnx` if you quantized them, plain `.onnx`
if not, and match the filenames in `PARAKEET_FILES` in
`voice-type/voice_type/download.py`.

The model is `nvidia/parakeet-rnnt-1.1b`, CC-BY-4.0. Keep NVIDIA's attribution
in the HF repo card.

## 6. Pin the SHA-256s

```bash
sha256sum encoder.int8.onnx decoder.int8.onnx joiner.int8.onnx tokens.txt
```

Paste each hash into the matching entry in `PARAKEET_FILES` in
`voice-type/voice_type/download.py`. Until every `sha256` is filled,
`download_parakeet_model()` refuses to run (so we never ship an unverified
model). That is the last step that flips first-run STT on.
