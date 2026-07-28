# A REST API in Cryo → a standalone `.exe`

`server.cryo` compiles to a single native executable. Run it and the API is
live — no VM, no Python, no Go toolchain at runtime.

```bash
python Burnout/cryoc.py Cryo/examples/api/server.cryo --backend go -o build/api/server.go
```

That writes `build/api/server.exe` (~8.8 MB). Open it:

```text
cryo-api listening on http://localhost:8080
  GET  /api/health
  GET  /api/stats
  GET  /api/tasks
  GET  /api/tasks/{id}
  POST /api/tasks       body: {"title":"..."}
```

`PORT=9000 server.exe` serves elsewhere.

## The endpoints

| Method | Path | Response |
|---|---|---|
| GET | `/api/health` | `{"status":"ok","service":"cryo-api","version":"1.1.0","tasks":3}` |
| GET | `/api/tasks` | the full list |
| GET | `/api/tasks/{id}` | one task, or `404 {"error":"no task with that id"}` |
| GET | `/api/stats` | `{"total":3,"done":1,"pending":2,"percent_done":33}` |
| POST | `/api/tasks` | `201` with the created task, or `400` with the reason |

```bash
curl http://localhost:8080/api/tasks
curl -X POST http://localhost:8080/api/tasks -d '{"title":"ship it"}'
```

## How the two halves split

**Cryo owns the behaviour.** The `Task` struct, every JSON payload
(`json_encode` over the struct), the validation rules (`validate_title`,
`normalize_title`) and the arithmetic (`percent_done`). Every byte the API
returns is built by a Cryo function.

**The `>Go( ... )` block owns only transport** — the in-memory store, routing,
HTTP methods and status codes. It never decides *what* to say, only how to
put it on the wire.

Cryo functions keep their names in the generated Go, so the block calls
`task_json(...)` and `validate_title(...)` directly. `library >Go net/http<`
pulls each real dependency into the generated program's import block.

## Why a foreign block, and not `http_serve`

`http_serve(port, dir)` — the builtin behind
[`examples/fullstack/`](../fullstack/) — is a **static file server**. It has no
per-request handler, so it cannot express routing, methods, request bodies or
computed responses. A dynamic API needs the runtime to call back into Cryo code
on every request, which is not something the builtin does today.

Foreign blocks are the language's designed escape hatch for precisely this, and
they keep the API's *logic* in Cryo while borrowing a mature HTTP stack.

> Wanting this without a foreign block is reasonable, and it is a real gap: a
> native like `http_api(port, handler)` taking a Cryo function value would close
> it. The pieces exist — function values landed on the VM in 10.6 — but the
> callback has to be threaded through the Go VM, the C VM and the AOT route
> together to keep Pyro parity, so it is a feature in its own right rather than
> a tweak.

## Tests

```bash
python Burnout/tests/test_api.py
```

29 assertions: builds the executable, launches **only that file** on a free
port, and checks every endpoint, status code and payload — including the things
Cryo decides, like the field order `json_encode` emits, the whitespace trimming,
the inclusive 60-character boundary, and the truncating integer percentage.
Skips cleanly when the Go toolchain is absent.
