# Local Ollama API

Run the **Local Ollama API** workflow from the Actions tab, selecting an Ollama model. The workflow starts Ollama on the runner, warms the model with a one-token generation, and publishes a Cloudflare Quick Tunnel endpoint in the chat frontend. It shuts down after five minutes without API activity, or at the six-hour job timeout.

## Startup caching

The workflow caches the pinned Ollama CPU installation, cloudflared, and model weights separately. Model caches are keyed by the full model name and tag. On a cache hit, the workflow skips the model pull; it still starts a new server and runs a warm-up before publishing the endpoint. The model stays loaded while the server is running, so the first chat request does not have to load it. The initial run for a model, or a run after cache eviction, still has to download it. Inference itself can still be slow on a CPU runner, but the frontend streams response text as it is generated.

GitHub Actions caches are subject to branch visibility, retention and repository storage limits. Large models may exceed the available cache quota; cache save failures do not stop the live server, but a later run will have to download again. Caches are saved after the endpoint is published, before the long-running wait. If the job is cancelled before a save completes, that cache may not be available next time.

Ollama model tags can change upstream. Because a cache hit intentionally skips `ollama pull`, delete that model's cache in the repository's Actions cache settings (or bump the model cache key version in the workflow) to fetch an updated tag. Bump `OLLAMA_VERSION` or `CLOUDFLARED_VERSION` in the workflow to update either tool and invalidate its cache.

The Quick Tunnel exposes an unauthenticated Ollama API to anyone with its URL. Do not use it for sensitive data or share the URL with untrusted users.

## Testing streaming

Run `python3 tests/test_proxy_streaming.py` and `node tests/test_chat_streaming.cjs` to check incremental proxy delivery, browser-side parsing, and error handling without starting Ollama.
