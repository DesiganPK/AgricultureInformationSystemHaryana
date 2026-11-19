"""Scoring for the base-vs-adapter comparison (M18). Used by the notebook and testable without a GPU.

A case is one held-out example. Its answer is a small JSON object. A FIELD is one thing the model had to decide:
  map_columns   (column, crop) / (column, measure) / (column, unit) for every column, plus row_label
  route         agent
  doc_context   district, year
A field counts as correct only on an exact match after the same normalisation the pipeline applies (crop names through the crop list).
precision = correct / fields the model filled (non-null); recall = correct / fields the reference fills; F1 = 2PR / (P + R).
An answer that is not valid JSON scores zero on all of its fields. Null means 'not stated': saying null where the reference is null is
not rewarded (it is not a filled field), but inventing a value there lowers precision."""
import csv, datetime as dt, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)


def load_jsonl(path):
    with open(path, encoding="utf-8") as f: return [json.loads(l) for l in f if l.strip()]


def parse(text):
    from llm import parse_json
    return parse_json(text)


def fields(task, obj):
    """Answer object -> {field key: value} with nulls kept as None."""
    if not isinstance(obj, dict): return {}
    if task == "route": return {"agent": obj.get("agent")}
    if task == "doc_context": return {"district": _district(obj.get("district")), "year": obj.get("year")}
    from table_mapper import canon_crop
    out = {"row_label": obj.get("row_label")}
    for e in obj.get("columns") or []:
        if not isinstance(e, dict) or "c" not in e: continue
        crop = e.get("crop")
        out[f"c{e['c']}.crop"] = (canon_crop(crop) or str(crop)) if crop else None
        out[f"c{e['c']}.measure"] = e.get("measure") if e.get("measure") not in (None, "other") else None
        out[f"c{e['c']}.unit"] = e.get("unit")
    return out


def _district(d):
    from district_names import canonical
    return canonical(d) if isinstance(d, str) else None


def score_case(task, reference_text, predicted_text):
    """-> dict(valid_json, correct, predicted, reference) field counts for one case."""
    ref = fields(task, json.loads(reference_text)); obj = parse(predicted_text); pred = fields(task, obj) if obj is not None else {}
    n_ref = sum(v is not None for v in ref.values()); n_pred = sum(v is not None for v in pred.values())
    correct = sum(1 for k, v in ref.items() if v is not None and pred.get(k) == v)
    return dict(valid_json=obj is not None, correct=correct, predicted=n_pred, reference=n_ref)


def f1(correct, predicted, reference):
    p = correct / predicted if predicted else 0.0; r = correct / reference if reference else 0.0
    return (2 * p * r / (p + r) if p + r else 0.0), p, r


def compare(cases, base_out, adapter_out):
    """cases: held-out examples; base_out / adapter_out: {case id: model text}. -> (summary rows, per-case rows)."""
    per, agg = [], {}
    for c in cases:
        ref = c["messages"][2]["content"]
        b = score_case(c["task"], ref, base_out.get(c["id"], "")); a = score_case(c["task"], ref, adapter_out.get(c["id"], ""))
        per.append(dict(case_id=c["id"], task=c["task"], label_kind=c["label_kind"], source=c["source"], source_sha256=c["source_sha256"], reference_fields=b["reference"],
                        base_valid_json=int(b["valid_json"]), base_correct=b["correct"], base_predicted=b["predicted"],
                        adapter_valid_json=int(a["valid_json"]), adapter_correct=a["correct"], adapter_predicted=a["predicted"]))
        for scope in (c["task"], "all"):
            g = agg.setdefault(scope, dict(n=0, ref=0, bc=0, bp=0, bv=0, ac=0, ap=0, av=0))
            g["n"] += 1; g["ref"] += b["reference"]; g["bc"] += b["correct"]; g["bp"] += b["predicted"]; g["bv"] += b["valid_json"]
            g["ac"] += a["correct"]; g["ap"] += a["predicted"]; g["av"] += a["valid_json"]
    summary = []
    for scope, g in agg.items():
        fb, pb, rb = f1(g["bc"], g["bp"], g["ref"]); fa, pa, ra = f1(g["ac"], g["ap"], g["ref"])
        summary.append(dict(scope=scope, cases=g["n"], reference_fields=g["ref"], f1_base=round(fb, 4), precision_base=round(pb, 4), recall_base=round(rb, 4),
                            f1_adapter=round(fa, 4), precision_adapter=round(pa, 4), recall_adapter=round(ra, 4), gain_f1=round(fa - fb, 4),
                            valid_json_base=round(g["bv"] / g["n"], 4), valid_json_adapter=round(g["av"] / g["n"], 4)))
    return sorted(summary, key=lambda r: (r["scope"] != "all", r["scope"])), per


def write_comparison(summary, per, manifest, out_dir, base_model, adapter_name, train_info=""):
    """Writes eval/adapter_comparison.csv (one row per scope) and eval/results/adapter_cases.csv (one row per held-out case)."""
    os.makedirs(os.path.join(out_dir, "results"), exist_ok=True)
    extra = dict(base_model=base_model, adapter=adapter_name, date=dt.date.today().isoformat(), train_examples=manifest.get("train_examples"), heldout_examples=manifest.get("heldout_examples"),
                 heldout_real_files=len(manifest.get("heldout_files", {})), overlap_files_by_sha256=manifest.get("overlap_files_by_sha256"),
                 overlap_examples_by_prompt_hash=manifest.get("overlap_examples_by_prompt_hash"), training=train_info)
    p1 = os.path.join(out_dir, "adapter_comparison.csv")
    with open(p1, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(summary[0]) + list(extra)); w.writeheader()
        for r in summary: w.writerow({**r, **extra})
    p2 = os.path.join(out_dir, "results", "adapter_cases.csv")
    with open(p2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(per[0])); w.writeheader(); w.writerows(per)
    return p1, p2


def chat_text(tokenizer, messages, add_generation_prompt=False):
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=add_generation_prompt)


def encode_example(tokenizer, messages, max_len):
    """Token ids for one training example with the loss limited to the assistant's answer (prompt tokens get label -100)."""
    prompt = chat_text(tokenizer, messages[:2], add_generation_prompt=True); full = chat_text(tokenizer, messages)
    p_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]; f_ids = tokenizer(full, add_special_tokens=False)["input_ids"][:max_len]
    labels = [-100] * min(len(p_ids), len(f_ids)) + f_ids[len(p_ids):]
    return dict(input_ids=f_ids, attention_mask=[1] * len(f_ids), labels=labels[:len(f_ids)])


def generate(model, tokenizer, messages, max_new_tokens=600):
    import torch
    prompt = chat_text(tokenizer, messages[:2], add_generation_prompt=True)
    ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(model.device)
    cuda = model.device.type == "cuda"
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16, enabled=cuda):
        out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False, pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
    return tokenizer.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)


def collate(batch, pad_id):
    """Pad a list of encode_example() outputs to one tensor batch (labels padded with -100 so padding is not trained on)."""
    import torch
    n = max(len(b["input_ids"]) for b in batch)
    pad = lambda seq, v: seq + [v] * (n - len(seq))
    return dict(input_ids=torch.tensor([pad(b["input_ids"], pad_id) for b in batch]), attention_mask=torch.tensor([pad(b["attention_mask"], 0) for b in batch]),
                labels=torch.tensor([pad(b["labels"], -100) for b in batch]))


CHATML = ("{% for m in messages %}{{ '<|im_start|>' + m['role'] + '\n' + m['content'] + '<|im_end|>' + '\n' }}{% endfor %}"
          "{% if add_generation_prompt %}{{ '<|im_start|>assistant\n' }}{% endif %}")


def tiny_model(texts, vocab=1500):
    """SMOKE TEST ONLY: a randomly initialised 2-layer model with Qwen's architecture and a tokenizer trained on the dataset text, built locally with
    no download. It lets the whole notebook run in a minute on a CPU to check the code. Its scores mean nothing."""
    from tokenizers import Tokenizer, models, trainers, pre_tokenizers, decoders
    from transformers import PreTrainedTokenizerFast, Qwen2Config, AutoModelForCausalLM
    tk = Tokenizer(models.BPE(unk_token=None)); tk.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False); tk.decoder = decoders.ByteLevel()
    tk.train_from_iterator(texts, trainers.BpeTrainer(vocab_size=vocab, special_tokens=["<|endoftext|>", "<|im_start|>", "<|im_end|>"], initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
    tok = PreTrainedTokenizerFast(tokenizer_object=tk, eos_token="<|im_end|>", pad_token="<|endoftext|>"); tok.chat_template = CHATML
    cfg = Qwen2Config(vocab_size=len(tok), hidden_size=64, intermediate_size=128, num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=4096,
                      eos_token_id=tok.eos_token_id, pad_token_id=tok.pad_token_id, bos_token_id=tok.eos_token_id)
    return tok, AutoModelForCausalLM.from_config(cfg)
