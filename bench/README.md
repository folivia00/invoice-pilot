# Memory benchmark

`memory.py` measures the bot process without network or Telegram: the real services,
dispatcher and Gemini provider, with an `httpx.MockTransport` answering from
`eval/recordings`. It reports, in MiB:

- **rest**: VmRSS after a warm-up (text PDF, scan PDF, photo, CSV and XLSX export) and `gc`;
- **peaks**: VmHWM, reset through `/proc/self/clear_refs` before each case: a 5 MB scan PDF,
  a 5 MB photo, and 10 uploads waiting in the queue while a 5 MB scan is processed.

It runs on Linux only. The host limits are reproduced with Docker:

```sh
docker run --rm --memory 256m --memory-swap 256m --cpus 0.5 \
  -v "$PWD:/src:ro" -v "/path/to/big-files:/big:ro" -v "/path/to/out:/out" \
  python:3.12-slim sh /src/bench/container.sh
```

`container.sh` installs the locked runtime dependencies only (`uv sync --frozen --no-dev`,
so no Pillow or other dev tools) with precompiled bytecode, as a server install should be
(otherwise the first start compiles every module and measures higher), writes
`pip-freeze.txt`, and runs the benchmark three times (`runs.jsonl`, one JSON line per
fresh process).

The 5 MB input files are not part of the repository: generate a scan PDF (5 image pages,
no text layer) and a JPEG just under the 5 MiB Compact limit, and mount them as `/big`
(`scan_5mb.pdf`, `photo_5mb.jpg`). Random-noise images reach that size because noise does
not compress; the bot never decodes images, so only the byte size matters.
