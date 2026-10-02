# clients/

One folder per client, one folder per project inside it. Configuration only — no
code lives here, and nothing here is imported. Every command in this library takes
`--project <folder>`, and a project folder is exactly what that argument expects.

A client project lives in **one of two homes**:

1. `clients/<client>/<project>` — this folder (the library's own convention), and
2. `Talan_Factory/Talan_usine_config/` — the factory's config home (the folder
   itself is a project, or contains project folders).

`--project` also accepts a bare name instead of a path: if the value isn't an
existing folder, the CLI looks for a folder called that (case- and
punctuation-insensitive) under both homes, so `--project talan_usine_config`
finds `Talan_Factory/Talan_usine_config` on its own. A name matching nothing, or
matching more than one folder, is reported as an error rather than guessed.

```
Talan_Factory/Talan_usine_config/            (a project folder, the factory home)
  .env                                       credentials for this project     (gitignored)
  .env.example                               the shape of the above           (committed)
  Config_Connection_Sources/
    sources.json                             what to connect to and watch     (gitignored)
    sources.example.json                     the shape of the above           (committed)
  scripts/populate_demo.py                   idempotent demo population
  scripts/export_graph.py                    graph -> portable JSON export
  _bmad_input/                               exported ALM items               (runtime)
  _bmad_state/                               state.json, snapshots, history   (runtime)
```

## Why the split

A project's two config files carry a site URL, an API token and a JQL query — the
things that differ per client and must never end up in a shared repository. Both
are gitignored; their `*.example` twins are committed, so a clone shows what a new
project needs to fill in without ever shipping what one project actually holds.

Everything that runs is in `connection_sources/`. Adding a client is creating a
folder and filling in two files; it never involves touching the library.

## Adding a project

```bash
mkdir -p clients/<client>/<project>/Config_Connection_Sources
cp Talan_Factory/Talan_usine_config/.env.example clients/<client>/<project>/.env
cp Talan_Factory/Talan_usine_config/Config_Connection_Sources/sources.example.json \
   clients/<client>/<project>/Config_Connection_Sources/sources.json
```

Fill both in, then check the connection before scheduling anything:

```bash
python -m connection_sources.cli health --project clients/<client>/<project>
```

## Running against one

```bash
python -m connection_sources.update_state --project Talan_Factory/Talan_usine_config
```

`_bmad_state/` and `_bmad_input/` are created on first run. Neither is
configuration: `_bmad_state/` is this project's memory of what the tracker looked
like last time, and deleting it makes the next run treat every item as new.