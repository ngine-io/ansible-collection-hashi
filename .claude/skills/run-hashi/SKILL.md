---
name: run-hashi
description: Build, run, test and drive the ngine_io.hashi Ansible collection. Use when asked to run, launch, start, build, test, lint, validate or smoke-test this collection, to stand up a live Nomad or Consul agent from its role templates, or to exercise its nomad_node / nomad_job / nomad_agent_info / nomad_raft_info modules against a real cluster.
---

# Running ngine_io.hashi

This collection has no UI and no server of its own. What it produces is
**Nomad and Consul configuration**, plus **modules that drive their HTTP
APIs**. So "running it" means: render the role templates, hand them to real
`nomad` and `consul` binaries, and point the collection's own modules at the
agents that come up.

`.claude/skills/run-hashi/driver.sh` does all of that. Prefer it over ad hoc
commands — it works around several things that will otherwise cost you an
hour (see Gotchas).

All paths below are relative to the collection root (the directory holding
`galaxy.yml`).

## Prerequisites

Only `python3`, `curl`, `unzip` and `rsync` are needed up front. The driver
creates its own venv and downloads the `nomad` and `consul` binaries if they
are not already on `PATH` (verified from a stripped `PATH`: both are fetched
from releases.hashicorp.com).

Check what you have:

```bash
for p in python3-venv curl unzip rsync; do
  dpkg -s "$p" >/dev/null 2>&1 && echo "$p: installed" || echo "$p: MISSING"
done
```

All four were already present in this container. If any is missing, install it
with `sudo apt-get update && sudo apt-get install -y <packages>`.

Everything the driver creates lives in `$HASHI_RUN_DIR` (default
`/tmp/hashi-run`). The repo is never written to.

## Agent path: the driver

```bash
./.claude/skills/run-hashi/driver.sh setup      # venv, binaries, collection tree
./.claude/skills/run-hashi/driver.sh check      # sanity + units + lint + render
./.claude/skills/run-hashi/driver.sh validate   # real nomad/consul config validate
./.claude/skills/run-hashi/driver.sh up         # start live agents
./.claude/skills/run-hashi/driver.sh drive      # exercise modules + roles
./.claude/skills/run-hashi/driver.sh status     # what is running
./.claude/skills/run-hashi/driver.sh down       # stop
./.claude/skills/run-hashi/driver.sh all        # all of the above in order
```

`setup` takes a few minutes the first time (pip + two binary downloads);
afterwards it is fast. `all` is the one-shot entry point.

### What each step proves

`check` runs the collection's own gates:

```
ansible-test sanity   24 of the 34 tests run here; the rest skip because
                      shellcheck and older python interpreters are absent
ansible-test units    41 tests (25 modules + 16 module_utils)
ansible-lint          production profile, 0 failures, 3 known warnings
ansible-playbook --syntax-check tests/playbooks/*.yml
tests/render_templates.py   12 render cases, parsed as HCL
```

The three lint warnings are the `hashi_common__*` variables recorded in
`.ansible-lint-ignore`; they are expected.

`validate` renders five role profiles (`nomad-server`, `nomad-client`,
`nomad-both`, `consul-server`, `consul-client`) and hands each to the real
binary. Expected output:

```
=== nomad config validate: nomad-server ===
Configuration is valid!
...
=== consul validate: consul-server ===
"autopilot.disable_upgrade_migration" is a Consul Enterprise configuration and will have no effect
bootstrap_expect > 0: expecting 3 servers
Configuration is valid!
```

`up` renders a one-node server+client Nomad and a one-node Consul **from the
role templates** and starts them:

```
=== status ===
nomad   up   leader="127.0.0.1:4647"
consul  up   leader="127.0.0.1:8300"
node    node1 status=ready eligibility=eligible
```

`drive` runs `drive.yml` against those agents. It asserts rather than just
printing, so a regression fails the play. Last run: `ok=22 changed=7 failed=0`,
ending with the real Consul catalog:

```
{"consul":[],"node-exporter":["metrics"],"nomad":["http","serf","rpc"],"nomad-client":["http"]}
```

(`nomad` and `nomad-client` appear because the driver renders with
`nomad__use_consul=true`, so the Nomad agent registers itself.)

### Driving a single module by hand

The agents from `up` are ordinary agents. Point anything at them:

```bash
curl -sS http://127.0.0.1:4646/v1/status/leader
curl -sS http://127.0.0.1:8500/v1/catalog/services | jq .

/tmp/hashi-run/venv/bin/ansible -i localhost, -c local localhost \
  -e ansible_python_interpreter=/tmp/hashi-run/venv/bin/python \
  -m ngine_io.hashi.nomad_raft_info -a 'url=http://127.0.0.1:4646'
```

Set `ANSIBLE_COLLECTIONS_PATH=/tmp/hashi-run` for that to resolve.

### Rendering a config without starting anything

```bash
/tmp/hashi-run/venv/bin/python .claude/skills/run-hashi/render_live.py \
  "$PWD" /tmp/out nomad-both \
  'nomad__client_plugin_raw_exec_enabled=true' 'nomad__acl_enabled=true'
```

Any `key=value` after the profile is a role variable; values are parsed as
JSON when possible, so `'nomad__servers=["10.0.0.1"]'` works.

## Human path

There is no app to open. A human runs the collection against real hosts with
`ansible-playbook`, using the layout in `README.md`. The playbooks under
`tests/playbooks/` are the documented ones and are syntax-checked in CI, but
they need real systemd hosts — they will not run against this container.

## Gotchas

- **`nomad config validate` does not validate task driver plugin config.**
  This is the big one. A bad option inside `plugin "raw_exec" { config { ... } }`
  passes validation and then the agent refuses to start with
  `failed to create plugin loader`. That is how the `no_cgroups` bug
  (removed from raw_exec in Nomad 1.7, fixed in 1.8.0) was found. **`validate`
  is not enough — always run `up` too.**
- **Validate the directory, not a file.** The role splits config across
  `nomad.hcl`, `server.hcl` and `client.hcl` and Nomad merges them. Validating
  one file alone fails with `Must specify either server, client or dev mode`
  or `Must specify "data_dir"`.
- **`bootstrap_expect` comes from the inventory group size**, which is 3 in the
  test fixtures. A one-node cluster never elects a leader unless you override
  `nomad__server_bootstrap_expect=1` / `consul__bootstrap_expect=1`. The driver
  does this.
- **Gossip keys must be real base64.** A placeholder gives
  `Invalid encryption key: illegal base64 data`. Use
  `nomad operator gossip keyring generate` and `consul keygen`.
- **`ansible-test` refuses a symlinked collection root** and requires an
  `ansible_collections/<ns>/<name>` parent directory. This repo lives at
  `.../ngine_io/hashi`, which has no such parent, so the driver `rsync`es a
  real copy to `/tmp/hashi-run/ansible_collections/ngine_io/hashi`. A symlink
  gives `FATAL: The current working directory must be within the source tree
  being tested`.
- **`community.general.consul` needs `py-consul==1.2.4`.** Newer releases fail
  with `type object 'Consul' has no attribute 'Agent'` — the module still uses
  the pre-1.3 API. 1.5.5 and 1.7.1 both break; the driver pins 1.2.4.
- **With `-c local`, Ansible discovers the *system* python**, not the venv, so
  `py-consul` appears missing. Pass
  `-e ansible_python_interpreter=/tmp/hashi-run/venv/bin/python`.
- **A Nomad client runs fine unprivileged here**, contrary to expectation. It
  registers as a node, which is all `nomad_node` needs. The `docker` and `exec`
  drivers are unavailable, so jobs stay `pending` — `nomad_job` reports this
  honestly in `warnings_from_plan`, and `drive.yml` expects it.
- **`nomad_node` against a server-only agent fails on purpose** with
  `The agent at ... is not a client, so it has no node to act on`. Pass `name`
  or `node_id` to address a client from elsewhere.
- **Consul warns that `autopilot.disable_upgrade_migration` is Enterprise-only.**
  Harmless; the role renders it unconditionally.
- **`changelogs/` is excluded from ansible-lint.** antsibull-changelog
  generates YAML whose indentation the production profile rejects.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `FATAL: The current working directory must be within the source tree being tested` | You ran `ansible-test` in the repo or through a symlink. Use the driver, which copies to `/tmp/hashi-run/ansible_collections/ngine_io/hashi`. |
| `Error starting agent: failed to create plugin loader ... Invalid label` | A plugin option this Nomad version dropped. `nomad config validate` will not catch it; read `/tmp/hashi-run/live/nomad.log`. |
| `Invalid encryption key: illegal base64 data at input byte 6` | Placeholder gossip key. Generate a real one. |
| `py-consul required for this module` | Either py-consul is missing, or Ansible is using the system python. Pass `ansible_python_interpreter`. |
| `type object 'Consul' has no attribute 'Agent'` | py-consul too new. `pip install 'py-consul==1.2.4'`. |
| `curl: (7) Failed to connect to ... 4646` | The agent died on startup. `tail -30 /tmp/hashi-run/live/nomad.log`. |
| Nomad has no leader, `/v1/status/leader` is `""` | `bootstrap_expect` > number of servers. |
| `down` says stopped but 4646 is still open | Fixed: `down` now falls back to matching agents by command line and escalates to SIGKILL. If you see this, something outside `$HASHI_RUN_DIR` is holding the port. |
| `ansible-galaxy: unrecognized arguments: -q` | `ansible-galaxy` has no `-q`; use `--force`. |

## Files

```
.claude/skills/run-hashi/
  SKILL.md              this file
  driver.sh             the harness: setup / check / validate / up / drive / down
  render_live.py        renders role templates into a runnable config dir
  drive.yml             the playbook drive runs; asserts, so regressions fail
  fixtures/echo.nomad   job spec used to exercise nomad_job
```
