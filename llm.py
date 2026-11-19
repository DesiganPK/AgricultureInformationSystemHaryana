"""LLM layer for AgriAgent.  One function matters: ask_json(task, system, user) -> dict or None.

The LLM is used for MEANING only (which agent, what a column is, which district a document is about).
It never supplies a number: every value in the database is copied from the source by code.
If the LLM is off, unreachable, slow or returns bad JSON, ask_json returns None and the caller uses its fixed rules.

Choose the backend with --llm on run.py or the AGRI_LLM environment variable:
  off      rules only (no model)
  auto     (default) use Ollama if it is running locally and has the model, otherwise rules only
  ollama   local Ollama server            AGRI_LLM_MODEL=qwen2.5:1.5b            AGRI_LLM_URL=http://localhost:11434
  hf       Hugging Face transformers      AGRI_LLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct   AGRI_LLM_ADAPTER=<folder of the fine-tuned LoRA adapter>
  openai   any OpenAI-compatible server (vLLM, LM Studio, llama.cpp)   AGRI_LLM_URL=http://localhost:8000/v1   AGRI_LLM_KEY=...
  stub     a Python function registered with set_stub() (used by the tests)
Every call is appended to logs/llm_calls.jsonl (task, backend, model, seconds, tokens, valid JSON or not, retry), so call counts,
latency and tokens per document can be reported."""
import sys, hashlib, json, os, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = {"ollama": "qwen2.5:1.5b", "hf": "Qwen/Qwen2.5-1.5B-Instruct", "openai": "Qwen/Qwen2.5-1.5B-Instruct", "stub": "stub"}
STATS = dict(calls=0, valid=0, retries=0, cache_hits=0, seconds=0.0, tokens_in=0, tokens_out=0, accepted=0, rejected=0, over_cap=0, labels_filled=0, labels_agree=0, labels_differ=0)
_STATE = dict(backend=None, model=None, note="", stub=None, hf=None, cache={})


def _env(k, d=None): return os.environ.get(k, d)
def log_path(): return _env("AGRI_LLM_LOG", os.path.join(HERE, "logs", "llm_calls.jsonl"))
def set_stub(fn): _STATE["stub"] = fn
def reset():
    """Forget the chosen backend and the cache (tests, or after changing environment variables)."""
    _STATE.update(backend=None, model=None, note="", hf=None, cache={})
    for k in STATS: STATS[k] = 0 if k != "seconds" else 0.0


def _http(url, payload, headers=None, timeout=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout or float(_env("AGRI_LLM_TIMEOUT", "120"))) as r:
        return json.loads(r.read().decode("utf-8"))


def _ollama_has(url, model):
    try: tags = _http(url.rstrip("/") + "/api/tags", None, timeout=2)
    except Exception as e: return False, f"Ollama not reachable at {url} ({type(e).__name__})"
    names = [m.get("name", "") for m in tags.get("models", [])]
    if model in names or any(n.split(":")[0] == model for n in names): return True, ""
    return False, f"Ollama is running but model '{model}' is not pulled (run: ollama pull {model})"


def backend():
    """Resolve the backend once per process. Returns (name, model, note). name == 'off' means rules only."""
    if _STATE["backend"]: return _STATE["backend"], _STATE["model"], _STATE["note"]
    want = (_env("AGRI_LLM", "auto") or "auto").lower(); note = ""
    if want == "auto":
        url = _env("AGRI_LLM_URL", "http://localhost:11434"); model = _env("AGRI_LLM_MODEL", DEFAULT_MODEL["ollama"])
        ok, note = _ollama_has(url, model)
        want = "ollama" if ok else "off"
    if want == "stub" and _STATE["stub"] is None: want, note = "off", "stub backend asked for but no stub registered"
    if want not in ("off", "ollama", "hf", "openai", "stub"): want, note = "off", f"unknown AGRI_LLM value; using rules only"
    _STATE.update(backend=want, model=None if want == "off" else _env("AGRI_LLM_MODEL", DEFAULT_MODEL[want]), note=note)
    return _STATE["backend"], _STATE["model"], _STATE["note"]


def enabled(): return backend()[0] != "off"
def describe():
    b, m, note = backend()
    if b == "off": return "LLM off (rules only)" + (f": {note}" if note else "")
    ad = _env("AGRI_LLM_ADAPTER")
    return f"LLM {b}:{m}" + (f" + adapter {ad}" if b == "hf" and ad else "")


# ---------------------------------------------------------------- backends: each returns (text, tokens_in, tokens_out)
def _call_ollama(model, system, user, max_tokens):
    url = _env("AGRI_LLM_URL", "http://localhost:11434").rstrip("/") + "/api/chat"
    r = _http(url, {"model": model, "stream": False, "format": "json", "options": {"temperature": 0, "num_predict": max_tokens},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    return r.get("message", {}).get("content", ""), r.get("prompt_eval_count", 0), r.get("eval_count", 0)


def _call_openai(model, system, user, max_tokens):
    url = _env("AGRI_LLM_URL", "http://localhost:8000/v1").rstrip("/") + "/chat/completions"
    key = _env("AGRI_LLM_KEY", "")
    r = _http(url, {"model": model, "temperature": 0, "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
              headers={"Authorization": f"Bearer {key}"} if key else None)
    u = r.get("usage", {})
    return r["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def _call_hf(model, system, user, max_tokens):
    """transformers on this machine; loads once. With AGRI_LLM_ADAPTER set, the fine-tuned LoRA adapter is applied on top of the base model."""
    if _STATE["hf"] is None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        tok = AutoTokenizer.from_pretrained(model)
        gpu = torch.cuda.is_available()
        kw = dict(device_map="auto") if gpu else {}; dt_ = torch.float16 if gpu else torch.float32
        try: mdl = AutoModelForCausalLM.from_pretrained(model, dtype=dt_, **kw)              # transformers 5 name
        except TypeError: mdl = AutoModelForCausalLM.from_pretrained(model, torch_dtype=dt_, **kw)   # transformers 4 name
        ad = _env("AGRI_LLM_ADAPTER")
        if ad:
            from peft import PeftModel
            mdl = PeftModel.from_pretrained(mdl, ad)
        mdl.eval(); _STATE["hf"] = (tok, mdl)
    import torch
    tok, mdl = _STATE["hf"]
    prompt = tok.apply_chat_template([{"role": "system", "content": system}, {"role": "user", "content": user}], tokenize=False, add_generation_prompt=True)
    ids = tok(prompt, return_tensors="pt").to(mdl.device)
    with torch.no_grad(): out = mdl.generate(**ids, max_new_tokens=max_tokens, do_sample=False)
    new = out[0][ids["input_ids"].shape[1]:]
    return tok.decode(new, skip_special_tokens=True), int(ids["input_ids"].shape[1]), int(new.shape[0])


def _call_stub(model, system, user, max_tokens):
    out = _STATE["stub"](system, user)
    return (out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)), 0, 0


CALLERS = {"ollama": _call_ollama, "openai": _call_openai, "hf": _call_hf, "stub": _call_stub}


def parse_json(text):
    """First balanced {...} object in the text (models sometimes wrap JSON in prose or code fences). None if there is none."""
    if not text: return None
    start = text.find("{")
    while start >= 0:
        depth = 0; in_str = False; esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': in_str = False
            elif ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict): return obj
                    except ValueError: pass
                    break
        start = text.find("{", start + 1)
    return None


def _log(rec):
    try:
        p = log_path(); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError: pass


def ask_json(task, system, user, max_tokens=700, doc=""):
    """Ask the model for one JSON object. Returns the dict, or None (LLM off, error, timeout, or no valid JSON after one retry).
    Identical prompts within one run are answered from a cache (continuation tables repeat their header), so no call is repeated."""
    b, model, _ = backend()
    if b == "off": return None
    key = hashlib.sha256(f"{task}\0{system}\0{user}".encode()).hexdigest()
    if key in _STATE["cache"]:
        STATS["cache_hits"] += 1; return _STATE["cache"][key]
    if STATS["calls"] >= int(_env("AGRI_LLM_MAX_CALLS", "60")):             # bound the time one document can take; the rest of it runs on rules
        STATS["over_cap"] += 1; return None
    obj = None
    for attempt in (0, 1):
        t0 = time.time(); text, tin, tout, err = "", 0, 0, ""
        u = user if attempt == 0 else user + "\n\nYour last answer was not valid JSON. Answer with one JSON object only."
        try: text, tin, tout = CALLERS[b](model, system, u, max_tokens)
        except Exception as e: err = f"{type(e).__name__}: {str(e)[:150]}"
        secs = time.time() - t0; obj = parse_json(text) if not err else None
        STATS["calls"] += 1; STATS["retries"] += attempt; STATS["seconds"] += secs; STATS["tokens_in"] += tin; STATS["tokens_out"] += tout; STATS["valid"] += obj is not None
        _log(dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S"), task=task, doc=doc, backend=b, model=model, adapter=_env("AGRI_LLM_ADAPTER", ""), retry=attempt,
                  seconds=round(secs, 3), tokens_in=tin, tokens_out=tout, valid_json=obj is not None, error=err, prompt_sha=key[:12],
                  prompt_chars=len(system) + len(u), output=text[:2000]))
        if err and not STATS.get("first_error"): STATS["first_error"] = err
        if _env("AGRI_LLM_QUIET") != "1":           # one progress line per call, so a slow CPU run does not look frozen
            print(f"  LLM call {STATS['calls']}/{_env('AGRI_LLM_MAX_CALLS', '60')} {task}: {secs:.0f}s, " + ("valid JSON" if obj is not None else err or "no valid JSON"), file=sys.stderr, flush=True)
        if obj is not None or err: break            # a transport error is not retried here: the caller falls back to rules
    _STATE["cache"][key] = obj
    return obj


def note(accepted):
    """Callers report whether a model answer passed their checks (counted for the run summary)."""
    STATS["accepted" if accepted else "rejected"] += 1


if __name__ == "__main__":
    print(describe())
    if enabled(): print(ask_json("selftest", "You answer with JSON only.", 'Return {"ok": true, "crop": "<English name of the crop गेहूं>"}'))
