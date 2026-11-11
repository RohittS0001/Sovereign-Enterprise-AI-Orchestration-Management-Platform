# Fine-tuning (QLoRA) — run by hand, outside the platform

Status: **the pipeline and data are ready; no model has been fine-tuned yet.** The platform
runs on general-purpose instruct models (`LLM1_MODEL_NAME`, `LLM2_MODEL_NAME`) until you
train and deploy these adapters.

| File | What it is |
|---|---|
| `qlora_finetune.py` | The one file you run on a GPU machine: QLoRA training + evaluation for both models. Does not import the repo. |
| `data/planner_{train,eval}.jsonl` | 1,368 / 152 examples for LLM 1 (tool planning, `PlannerOutput` JSON). |
| `data/responder_{train,eval}.jsonl` | 1,080 / 120 examples for LLM 2 (grounded answers with `[S1]` citations). |
| `build_datasets.py` | Regenerates the data from the platform's real prompts and tool schemas (needs the repo). |
| `requirements.txt` | GPU-side dependencies (PyTorch, Transformers, PEFT, bitsandbytes, Accelerate). |

```bash
pip install -r finetuning/requirements.txt
python finetuning/qlora_finetune.py --check-data                 # CPU: validate + token lengths
python finetuning/qlora_finetune.py --task both --compare-base   # GPU: train + evaluate both
```

Defaults: planner `Qwen/Qwen3-4B-Instruct-2507`, responder `microsoft/Phi-4-mini-instruct`
(both ~4B parameters, Apache-2.0 / MIT), 4-bit NF4 + double quantization, LoRA rank 16 on all
linear layers, 3 epochs, lr 2e-4 cosine, paged 8-bit AdamW, loss on the reply only. Outputs
go to `finetuning/outputs/<task>/` (`adapter/`, `metrics.json`, `report.md`, `samples.jsonl`;
`merged/` with `--merge`). About 24 GB of VRAM fits the defaults; use
`--max-len 2048 --batch-size 1` on 16 GB.

Metrics: eval loss / perplexity; planner JSON + schema validity, done accuracy, tool exact
match and F1, argument exact match, hallucinated-tool and unsafe-write rates, per-scenario
accuracy; responder ROUGE-L, token F1, citation validity and presence, number grounding and
behaviour checks (missing data, pending approval, injection warning, failed steps). With
`--compare-base` the report puts the untuned model next to the fine-tuned one.

The data is synthetic: it teaches the output format and the safety behaviour the platform
expects, not facts about any organization. `tests/unit/evaluation/test_finetuning_data.py`
checks that gold answers score perfectly and broken ones do not.
