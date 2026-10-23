# LiteLLM Proxy & Integration (LXC 113)

Dokumentation des LiteLLM-Proxies auf LXC 113 (`192.168.0.211:4000`), seiner Fallback-Routen,
des OpenCode-Go-Abonnements und der Anbindung an die Agenten-Sandbox (CT 112).

## Architektur & Aufgabenverteilung (Hybrid-Ansatz)

Zur Wahrung der Stabilität, Budgetkontrolle und Unabhängigkeit (Regel 6) gilt eine strikte
Aufgabenteilung zwischen dem Proxy (LXC 113) und den Skripten im Worker (CT 112):

### Im Worker (`worker_tick.py` / CT 112)
1. **Pre-Flight-Probe (`probe_model`, synchron, 45s, `stream=False`):**
   Schützt die Worker-Slots vor toten 30-Minuten-Runs bei leeren oder abbrechenden Streaming-Verbindungen.
   Die Ausweichliste geht in Stephans Reihenfolge OpenCode Go (`coder-go`) → DeepSeek-API (`coder`)
   → OpenRouter (`coder-or`); die Probe nimmt das erste Modell, das antwortet (#293).
2. **Modellfamilien-Wahrheit (Regel 6):**
   `worker_tick.py` schreibt das genutzte Modell (`Worker: ...`) in den Pull Request.
   Der Reviewer (`review_tick.py`) prüft gegen `FAMILIES`, dass Worker und Reviewer nie derselben
   Familie entstammen.
3. **Spend-Tracking:**
   Über `GET /key/info` ermittelt der Worker den Spend-Stand vor und nach einem Agentenlauf und
   schreibt das Delta auf den Cent genau in `audit.jsonl` (`run_cost`).

### Im Proxy (`/etc/litellm/config.yaml` / LXC 113)
1. **Provider-Fallbacks innerhalb derselben Familie:**
   Fällt DeepSeek direkt aus oder ist überlastet (429), routet LiteLLM transparent auf
   OpenCode Go, OpenRouter oder Fireworks weiter, ohne dass sich die Modellfamilie ändert.
2. **OpenCode Go Flatrate-Integration:**
   Priorisiertes Routing auf das monatliche Go-Abo. Bei Erreichen des rollierenden Limits (HTTP 429)
   wechselt LiteLLM ohne Wartezeit (`RateLimitErrorRetries: 0`) auf Pay-as-you-go.
3. **Budgetierung & Token-Limits:**
   Master-Budget (`max_budget: 50`), Provider-Budgets und RPM/Parallel-Request-Drosselung.

---

## Konfiguration auf LXC 113

* **Umgebung & Schlüssel:** `/etc/litellm.env` (Modus `640 root:litellm`)
* **Konfiguration:** `/etc/litellm/config.yaml`
* **Dienstverwaltung:** `systemctl restart litellm`

### Modell-Katalog (Auszug Coder / OpenCode Go)

```yaml
model_list:
  # coder – DeepSeek Direct (Standard-Pay-as-you-go)
  - model_name: coder
    litellm_params:
      model: deepseek/deepseek-flash
      api_key: os.environ/DEEPSEEK_API_KEY
      timeout: 600
      stream_timeout: 90
    model_info:
      input_cost_per_token: 0.0000003
      cache_read_input_token_cost: 0.000000006
      output_cost_per_token: 0.0000012

  # coder-go – OpenCode Go Flatrate-Abo (DeepSeek V4.1 Flash)
  - model_name: coder-go
    litellm_params:
      model: openai/deepseek-v4.1-flash
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      rpm: 30
      max_parallel_requests: 3
      timeout: 600
      stream_timeout: 90
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0

  # coder-pro-go – OpenCode Go Flatrate-Abo (DeepSeek V4 Pro)
  # fuer Worker Stufe 2 / komplexe Aufgaben. Keine Token-Kosten.
  - model_name: coder-pro-go
    litellm_params:
      model: openai/deepseek-v4-pro
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      rpm: 20
      max_parallel_requests: 2
      timeout: 900
      stream_timeout: 120
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0

  # coder-kimi-go – OpenCode Go Flatrate-Abo (Kimi K2.7 Code)
  # fuer Worker Stufe 3 / Eskalation. Keine Token-Kosten.
  - model_name: coder-kimi-go
    litellm_params:
      model: openai/kimi-k2.7-code
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      rpm: 30
      max_parallel_requests: 3
      timeout: 600
      stream_timeout: 90
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0

  # coder-qwen-go – OpenCode Go Flatrate-Abo (Qwen 3.8 Max)
  # fuer Worker (Qwen-Alternative). Keine Token-Kosten.
  - model_name: coder-qwen-go
    litellm_params:
      model: openai/qwen3.8-max
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      rpm: 30
      max_parallel_requests: 3
      timeout: 600
      stream_timeout: 90
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0

  # coder-mimo – Xiaomi MiMo-V2.6-Flash (Tau-Bench #7 Pareto: 78.7% Accuracy, OpenCode Go Flatrate)
  # Hochpraezises Modell fuer Worker. Keine Token-Kosten.
  - model_name: coder-mimo
    litellm_params:
      model: openai/mimo-v2.6-flash
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      rpm: 30
      max_parallel_requests: 3
      timeout: 600
      stream_timeout: 90
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0

  # reviewer-go – OpenCode Go Flatrate-Abo (GLM 5.3 Flash)
  # fuer Reviewer (GLM-Familie, unabhaengig von DeepSeek/Worker). Keine Token-Kosten.
  - model_name: reviewer-go
    litellm_params:
      model: openai/glm-5.3-flash
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      rpm: 30
      max_parallel_requests: 3
      timeout: 600
      stream_timeout: 90
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0

  # planner-go – OpenCode Go Flatrate-Abo (DeepSeek V4 Pro)
  - model_name: planner-go
    litellm_params:
      model: openai/deepseek-v4-pro
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      rpm: 20
      max_parallel_requests: 2
      timeout: 900
      stream_timeout: 120
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0

  # coder-nemotron-free – Kostenloses Modell (NVIDIA Nemotron 3.5 Lightning, 0$/M)
  - model_name: coder-nemotron-free
    litellm_params:
      model: openai/nemotron-3.5-lightning-free
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      timeout: 600
      stream_timeout: 90
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0

  # coder-minimax-free – Kostenloses Modell (MiniMax M3.1 Flash, 0$/M)
  - model_name: coder-minimax-free
    litellm_params:
      model: openai/minimax-m3.1-flash-free
      api_base: https://opencode.ai/zen/go/v1
      api_key: os.environ/OPENCODE_GO_KEY
      extra_headers:
        x-opencode-session: "sandbox-session"
        User-Agent: "opencode/1.0.0 (Linux; x86_64)"
      timeout: 600
      stream_timeout: 90
    model_info:
      input_cost_per_token: 0
      output_cost_per_token: 0
```

### Router & Fallback-Kaskade

```yaml
router_settings:
  fallbacks:
    - coder-go: ["coder", "coder-or"]   # OpenCode Go Limit (429) -> DeepSeek direkt -> OpenRouter
    - coder-pro-go: ["planner-pro"]     # OpenCode Go Pro (429) -> DeepSeek Pro OpenRouter
    - coder-kimi-go: ["coder-pro-go", "planner-pro"] # OpenCode Kimi (429) -> OpenCode Pro -> DeepSeek Pro OpenRouter
    - coder-qwen-go: ["coder-nvidia", "coder-go"]    # OpenCode Qwen (429) -> Qwen NVIDIA -> OpenCode Go
    - reviewer-go: ["coder-glm", "longctx-lite"]     # OpenCode GLM (429) -> OpenRouter GLM -> Gemini Lite
    - coder: ["coder-go", "coder-or"]   # DeepSeek direkt gestört -> OpenCode Go -> OpenRouter
    - planner-go: ["planner-pro"]

  model_group_retry_policy:
    coder-go:
      RateLimitErrorRetries: 0
      ServiceUnavailableErrorRetries: 0
      InternalServerErrorRetries: 0
      TimeoutErrorRetries: 0
      DefaultRetries: 0
    planner-go:
      RateLimitErrorRetries: 0
      ServiceUnavailableErrorRetries: 0
      InternalServerErrorRetries: 0
      TimeoutErrorRetries: 0
      DefaultRetries: 0
```

---

## Hinweise zu OpenCode Go

1. **Header `x-opencode-session`:**
   OpenCode verlangt bei API-Aufrufen über `https://opencode.ai/zen/go/v1` zwingend diesen Header.
   LiteLLM übergibt ihn über `extra_headers`.
2. **Datenschutz / Privacy-Einstellung in OpenCode Console:**
   Damit DeepSeek-Modelle geroutet werden dürfen, muss in der OpenCode-Konsole unter
   *Settings* → *Privacy* die Region auf **Global** gestellt sein.
3. **Verhalten bei Limit-Überschreitung (HTTP 429):**
   Das rollierende 5-Stunden- und Wochenfenster von OpenCode Go wird bei Überschreitung mit HTTP 429
   quittiert. LiteLLM erkennt dies durch `RateLimitErrorRetries: 0` sofort und routet transparent
   auf die Fallback-Route weiter.
