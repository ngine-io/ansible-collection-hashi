# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`ngine_io.hashi`, an Ansible collection that installs and configures Hashicorp
Nomad and Consul (roles) and drives Nomad's HTTP API (modules). There is no
application to run: the output is rendered HCL config plus modules that talk to
a live agent.

## Commands

`ansible-test` refuses a collection whose checkout is not inside an
`ansible_collections/<ns>/<name>` directory, and this repo is not (it sits at
`.../ansible-collection-hashi/ngine_io/hashi`, and a symlink does not satisfy
it either). The `run-hashi` skill copies the tree to
`/tmp/hashi-run/ansible_collections/ngine_io/hashi` and runs everything from
there — prefer it over invoking `ansible-test` by hand:

```bash
./.claude/skills/run-hashi/driver.sh setup     # venv, nomad/consul binaries, collection tree
./.claude/skills/run-hashi/driver.sh check     # sanity + units + lint + syntax + render
./.claude/skills/run-hashi/driver.sh validate  # real "nomad/consul config validate"
./.claude/skills/run-hashi/driver.sh up        # live one-node Nomad + Consul from the role templates
./.claude/skills/run-hashi/driver.sh drive     # exercise the modules and roles against them
./.claude/skills/run-hashi/driver.sh down
./.claude/skills/run-hashi/driver.sh all
```

Read [.claude/skills/run-hashi/SKILL.md](.claude/skills/run-hashi/SKILL.md)
before doing any live work; it lists the traps (notably that
`nomad config validate` does *not* check task driver plugin config, so
`validate` passing is not enough — `up` has to run too).

The individual gates, if you are running them in a correctly named tree:

```bash
ansible-lint                                  # production profile, must be clean
ansible-playbook -i tests/inventory --syntax-check tests/playbooks/*.yml
python3 tests/render_templates.py             # renders every template, parses as HCL
ansible-test sanity --docker
ansible-test units --docker
```

A single unit test, from the copied tree, needs the collection importable as
`ansible_collections.ngine_io.hashi`:

```bash
ansible-test units --docker tests/unit/plugins/modules/test_nomad_job.py
# or, with ANSIBLE_COLLECTIONS_PATH pointing at the parent of ansible_collections/
pytest tests/unit/plugins/modules/test_nomad_job.py -k drain
```

Changelog entries are antsibull-changelog fragments in `changelogs/fragments/`;
`CHANGELOG.rst` and `changelogs/changelog.yaml` are generated on release and are
excluded from ansible-lint.

## Architecture

### Roles produce config, modules drive the API

`roles/nomad` and `roles/consul` install the package, render config and own the
restart handler. Everything else is layered on: `roles/common` (the Hashicorp
apt/yum repo, a dependency of both), `roles/cni`, `roles/consul_service`
(wraps `community.general.consul`), `roles/nomad_job` and
`roles/nomad_upgrade`.

The last two are where roles and modules meet: they wrap their tasks in a
`module_defaults: {group/ngine_io.hashi.nomad: {...}}` block so connection
options are set once, then call the modules. `nomad_upgrade` is the interesting
one — it drains through `nomad_node`, upgrades, reboots, then blocks on
`nomad_agent_info` and `nomad_raft_info` until the host is back in the raft peer
set, which is what makes `serial: 1` actually safe.

### Module layer

`plugins/module_utils/nomad.py` holds everything shared:
`nomad_argument_spec()` (with `NOMAD_*` env fallbacks matching the CLI), the
`NomadAPI` wrapper around `fetch_url`, `parse_duration` for Go-style durations,
and `strip_volatile` which drops server-maintained job fields so `--diff` shows
only real changes. `plugins/doc_fragments/nomad.py` documents those same
options; every module extends `ngine_io.hashi.nomad` rather than repeating them.
New modules must also be added to the `nomad` action group in
[meta/runtime.yml](meta/runtime.yml), or `module_defaults` will not reach them.

Modules never shell out to the `nomad` binary — that is deliberate, so they
support check mode and `--diff` and can run under `delegate_to` from a host
with no Nomad installed. Two idempotency rules carry real logic:

- `nomad_node` decides "drained" from eligibility plus remaining non-terminal
  allocations, because Nomad clears the drain strategy when a drain completes
  but leaves the node ineligible.
- `nomad_job` gets idempotency from Nomad's `/plan` endpoint, not from text
  comparison, so reformatting a job spec is not a change. Unplaceable task
  groups come back in `warnings_from_plan`.

### Templating conventions

The collection is mostly Jinja, and neither ansible-test nor ansible-lint looks
inside a template — [tests/render_templates.py](tests/render_templates.py) is
the only thing that does. When you add an optional stanza, add a case there
with the variables that switch it on.

Two rules hold across all role defaults:

- A variable defaulting to `null` is omitted from the rendered config so the
  agent's own default applies. Templates guard with `{% if x is not none %}`.
- Anything not modelled explicitly goes into `nomad__extra_config` /
  `consul__extra_config`, written as JSON next to the HCL for the agent to
  merge. Nested mappings are rendered by the `hcl_body` macro in each role's
  `templates/_hcl_macros.j2`.

Variables are prefixed per role (`nomad__*`, `consul__*`, `cni__*`, …) and
named after the HCL stanza they belong to. The exception is `hashi_common__*`,
read by several roles, which is why `var-naming[no-role-prefix]` is waived for
`roles/common/defaults/main.yml` in `.ansible-lint-ignore`.

Both agent roles validate the rendered config before any restart handler fires,
so a bad inventory fails the play instead of leaving a crash-looping agent. Note
that config is split across `nomad.hcl`, `server.hcl` and `client.hcl` and
merged by Nomad — validation must target the directory, never a single file.

## Lint policy

`.ansible-lint` sets `profile: production` and CI fails on anything below it.
Exceptions are scoped narrowly: an inline `# noqa: <rule>` with a comment
explaining why (see the `package-latest` and `no-handler` waivers in
`roles/nomad_upgrade/tasks/main.yml`), or a path-and-rule line in
`.ansible-lint-ignore`. Never add a global `skip_list`.
