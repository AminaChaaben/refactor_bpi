# Connection Sources

ALM connection sources for the orchestrator: Jira, Xray, Azure DevOps, GitLab (REST), and
GitLab git-over-SSH. Installed as the `alm-conn` CLI (`connection_sources/cli.py`).

## SSH key vault (HashiCorp Vault)

`connection_sources/ssh_vault.py` stores every SSH private key used for git-over-SSH
(`clone`/`pull`/`push` in `connection_sources/clients/gitlab_git.py`) in HashiCorp Vault's
KV v2 secrets engine, addressed by an alias the caller chooses — never a path under
`~/.ssh`. Configured entirely through environment variables (`VAULT_ADDR`, `VAULT_TOKEN` or
`VAULT_ROLE_ID`/`VAULT_SECRET_ID`, `VAULT_AUTH_METHOD`, `VAULT_KV_MOUNT`) — see
`Talan_usine_config/.env.example` for the full list.

### Set up a local dev Vault

Dev mode auto-unseals and auto-enables a `secret/` KV v2 mount on start — there is no
`vault operator init`/`unseal`/`secrets enable` step. This is a throwaway instance only:
nothing survives `down`.

```bash
docker compose -f docker/docker-compose.vault.yml up -d
curl http://localhost:8200/v1/sys/health
```

Copy `Talan_usine_config/.env.example` to `.env` if you haven't already — it already has the
dev Vault's fixed root token filled in (`VAULT_TOKEN=talan-dev-root-token`).

### Verify and import a key

```bash
uv run alm-conn ssh-key-list
```

Expect `{"aliases": []}` against a fresh Vault.

```bash
uv run alm-conn ssh-key-import --alias <alias> --path <path-to-a-private-key-file>
```

### Migrating a key from the retired OS-keyring backend

Three routes, depending on what you still have:

- The original key file still exists — just re-run `ssh-key-import` above; no migration
  tooling needed.
- Only the old OS-keyring copy survives — install the `legacy-vault` extra
  (`pip install -e '.[legacy-vault]'`), then:
  ```bash
  uv run alm-conn ssh-key-migrate --alias <alias>   # or --all
  ```
  Verify the migrated key works (e.g. a real `git-pull` using it), then remove the old
  copy:
  ```bash
  uv run alm-conn ssh-key-legacy-purge --alias <alias> --confirm
  ```
  `ssh-key-migrate` refuses to overwrite an alias already present in the new Vault unless
  you pass `--force`.
- Fresh project, no prior key — skip straight to `ssh-key-import`.

### Pointing at a real (non-dev) Vault later

No code change — only config:

```bash
VAULT_ADDR=https://<your-vault-host>:8200
VAULT_AUTH_METHOD=approle
VAULT_ROLE_ID=<role-id>
VAULT_SECRET_ID=<secret-id>
```

Give that AppRole a policy scoped to `{VAULT_KV_MOUNT}/talan-alm-ssh-vault/*` only. Token
auth (the dev-mode path above) is not appropriate for a real deployment.

If this project also uses `connection_sources.env`'s Jira-credentials Vault fallback
(`JIRA_USERNAME`/`JIRA_API_TOKEN`, read from `Jira_username`/`Jira_token` at the mount
*root*, outside `talan-alm-ssh-vault/`), that AppRole additionally needs read access to
those two paths -- a policy scoped only to `talan-alm-ssh-vault/*` will not see them.
That fallback also only activates for token auth (`VAULT_TOKEN` set); it does not yet
check `VAULT_ROLE_ID`/`VAULT_SECRET_ID`.

### How to test it

Requires the dev Vault above to be running. Run from this directory.

```bash
python -m pytest tests/ -v
```

Covers `ssh_vault.py`'s store/get/delete/list-aliases and its refusal to import a
default-named identity from the OS-default SSH directory (unless `--force`), the
legacy-keyring migration commands (`ssh-key-migrate`, `ssh-key-legacy-purge`), and
`gitlab_git.push()`'s "no passphrase, no push" guard together with the CLI's stdin-only
passphrase contract. Tests that need the Vault skip cleanly (not fail) when it isn't
reachable, so the suite still runs for anyone not touching this feature.

### Isolated test-repo setup (never the real project's remote)

For trying out clone/pull/push without any risk of touching the real project's GitLab
remote, point the tooling at a separate, disposable test repo instead — never pass
`--project`, so none of this touches `Talan_Factory`'s own config.

`scripts/test-llmtester-env.ps1` sets up one PowerShell session for this in one step:
it pulls the test GitLab host's already-trusted key out of your own `~/.ssh/known_hosts`
(never fetches or trusts a fresh one), points `GITLAB_SSH_KNOWN_HOSTS` at that, sets the
dev Vault's address and token, and defines `$Test` as a destination folder outside this
repo entirely. Run from this directory, once per new terminal window (environment
variables set this way don't persist between windows):

```powershell
. .\scripts\test-llmtester-env.ps1
```

Then, still from this directory:

```bash
uv run alm-conn ssh-key-import --alias <alias> --path <path-to-a-key-file> --force
uv run alm-conn git-clone --repo-url <git@host:group/project.git> --dest $Test --ssh-key-alias <alias>
uv run alm-conn git-pull --repo-dir $Test --ssh-key-alias <alias>
```

If the imported key has its own passphrase, add `--prompt-passphrase` to `git-clone`/
`git-pull` — it prompts right there in the terminal (hidden input), never through a
frontend or a piped/logged value. Only pass it when a human is running the command by
hand; a scheduled task or spawned agent has no terminal to answer it and would hang.

A push from `$Test` always needs `--passphrase-stdin` (see below) regardless — that
one is never optional.

### Manual end-to-end push-approval walkthrough

This is the fuller loop, including the human-approval gate — see
[`../Orchestrateur_Lib/README.md`](../Orchestrateur_Lib/README.md#push-approval-approve-push)
for the `approve-push` half.

1. Bring up the dev Vault, import a read-only alias and a passphrase-protected push alias.
2. `uv run alm-conn git-clone` / `git-pull` unattended against the read-only alias.
3. Get a ticket to `READY_FOR_CODE_PUSH` in `state.json`, then run `approve-push` from
   `Orchestrateur_Lib`.
4. Confirm: the pending record clears only on push success; a deliberately wrong
   passphrase leaves it untouched and fails visibly; `orc-morning-review`'s report links
   to `approve-push` for that entry instead of the generic Approve/Reject/Modify prompt.
