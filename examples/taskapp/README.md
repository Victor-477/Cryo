# taskapp — a complete application, written entirely in Cryo

Roadmap **11.10**. A task tracker with an HTTP API, a browser UI, persistence
and auth. **No foreign blocks anywhere** — every line is Cryo, running on the
Pyro VM or compiled to a standalone binary.

```bash
python Burnout/cryoc.py Cryo/examples/taskapp/app.cryo --backend pyro -o build/taskapp.pyro --run
```

```bash
TASKS_TOKEN=secret python Burnout/pyro.py build Cryo/examples/taskapp/app.cryo -o build/taskapp
```

Then open **http://localhost:8080/?token=secret**.

`PORT`, `TASKS_TOKEN` and `TASKS_FILE` configure it. With no `TASKS_TOKEN`, auth
is off.

## The modules

| File | Role |
|---|---|
| `app.cryo` | configuration, routing, the accept loop |
| `tasks.cryo` | the domain: add / complete / delete / list, and its JSON |
| `store.cryo` | durable key-value storage on top of `write_file_atomic` |
| `http.cryo` | query parsing and the auth check |
| `web.cryo` | the browser UI, built as Cryo strings |

Each keeps **private state behind a `pub` API** — the shape that only became
possible once [ISSUES/19](../../../ISSUES/19-private-module-items-dropped.md)
was fixed.

## What it exercises

| | |
|---|---|
| **11.1** module state | every module holds its own data across requests |
| **11.6** HTTP natives | `http_listen` / `http_accept` / `http_respond` |
| **11.7** filesystem | `file_exists`, `read_file`, `env` |
| **11.8** durable writes | `write_file_atomic` — a crash cannot truncate the data |
| **10.8** modules | namespaced imports, `pub` visibility, private internals |

## API

| Method | Path | |
|---|---|---|
| GET | `/` | the UI |
| GET | `/api/tasks` | the list |
| POST | `/api/tasks` | body = the title |
| POST | `/api/tasks/complete?id=N` | |
| POST | `/api/tasks/delete?id=N` | |
| GET | `/api/stats` | totals |

```bash
curl "http://localhost:8080/api/tasks?token=secret"
curl -X POST "http://localhost:8080/api/tasks?token=secret" -d 'write the docs'
```

## Tests

```bash
python Burnout/tests/test_taskapp.py
```

30 assertions. It launches the app, exercises the API, then **restarts the
process on a different engine** against the same data file — which is how the
persistence and parity claims are checked rather than asserted. It also reads
the data file directly to confirm a delete reached the disk and no `.tmp` was
left behind.

## What building it revealed

The point of a reference application is to find what is missing. Two things:

- **`http_accept` exposes no request headers** — only method, path, query and
  body. So the auth token has to travel in the query string, where it lands in
  server logs and browser history. A real deployment wants `Authorization`.
  Headers are the clearest next addition to 11.6.
- **No percent-decoding.** `http.cryo` splits the query on `&` and `=` and
  stops there, so a value containing `%20` or `&` arrives raw. Fine for ids and
  a token; not fine in general. A `url_decode` builtin belongs alongside the
  HTTP natives.

Neither blocks the application, and both are recorded here rather than papered
over.
