# client_talan / project_1

Configuration for one project. Nothing here is code.

| File | Committed? | What it is |
| --- | --- | --- |
| `.env` | no, gitignored | Jira / Azure / GitLab credentials for this project |
| `.env.example` | yes | the variables the above must define |
| `Config_Connection_Sources/sources.json` | no, gitignored | what to connect to, what to watch |
| `Config_Connection_Sources/sources.example.json` | yes | the same file with every value blanked |
| `_bmad_input/` | runtime | exported ALM items, one JSON file per issue |
| `_bmad_state/` | runtime | `state.json`, snapshots, history, scheduler log |

## The `sync` block

Only `sync` affects change detection; the rest of `sources.json` is connection
config the library already understood.

| Key | Meaning |
| --- | --- |
| `interval_seconds` | how often the scheduler should run a cycle |
| `full_reconcile_every` | read the full scope every Nth cycle; the others read only what changed recently |
| `watch_buckets` | which buckets raise a signal. `["tests", "test_plans"]` means a change to a story is still recorded in `state.json` but does not queue work or write a history line. `"*"` signals everything |
| `export_on_change` | rewrite `_bmad_input/` when a watched change is seen |
| `sprint_field` | this site's sprint custom field id |
| `test_detail_fields` | our label → this site's custom field id, for the test-shape fields to watch |

## `test_detail_fields`

A custom field id is assigned by the Jira site that defines it, so the same
"Test Steps" is a different id on every instance and can never be hardcoded. Find
this site's ids with:

```bash
curl -u "$JIRA_USERNAME:$JIRA_API_TOKEN" "$JIRA_URL/rest/api/3/field" | \
  python -c "import json,sys; [print(f['id'], f['name']) for f in json.load(sys.stdin) if f.get('custom')]"
```

The label on the left is ours and appears in `state.json`, in the history line and
in the event `kind`. Four labels raise a named event — `test_type`, `test_steps`,
`precondition`, `expected_result` become `TEST_TYPE_CHANGED`, `TEST_STEPS_CHANGED`,
`PRECONDITION_CHANGED`, `EXPECTED_RESULT_CHANGED`. Any other label you declare is
still watched and still reported, as `TEST_DETAIL_CHANGED` carrying its label.

### Xray and this site

Xray stores a test's **Test Type** (Generic / Manual / Cucumber), its Gherkin
scenario, its steps and its preconditions in Xray's own database, not on the Jira
issue. On this instance those are reachable only through the Xray Cloud GraphQL API
at `https://xray.cloud.getxray.app/api/v2/`, which authenticates with its own
client id and secret — a `/rest/api/3/field` listing does not contain them, and a
`/rest/api/3/issue/DEM-15?fields=*all` read comes back without them.

What this instance *does* expose over Jira REST, and what `sources.json` therefore
declares, is:

| Label | Field | Id |
| --- | --- | --- |
| `precondition` | Precondition | `customfield_10169` |
| `test_steps` | Test Steps | `customfield_10167` |
| `expected_result` | Resultat Attendu | `customfield_10168` |
| `test_state` | Test State | `customfield_10357` |

Nothing in the engine is specific to those four. The day Test Type becomes readable
— through an Xray field mirrored onto the issue, or through an Xray API client
added alongside the Jira one — it is one line in `test_detail_fields` and
`TEST_TYPE_CHANGED` starts firing, with no code change.
