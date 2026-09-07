# Changelog

All notable changes to this collection are documented here.

## Unreleased

### Bugfixes

- `nomad_upgrade`: `change_when` was not a task keyword, so Ansible read it as
  a second module and aborted the play before the role could run.
- `nomad`: `nomad__client_meta` raised a `ValueError` as soon as it was set,
  because the template iterated the mapping without `.items()`.
- `nomad`: servers had no `retry_join`, so without Consul they never formed a
  cluster from `bootstrap_expect` alone.
- `nomad_upgrade`: the drain and eligibility commands only work on a client,
  but were run on server only nodes as well, where they always failed.
- `nomad_job`: the default address was `https://` while the agent serves plain
  HTTP unless TLS is enabled, so the role failed against its own defaults.
- `nomad`: the bridge networking sysctls are set after `br_netfilter` is
  loaded, instead of being wrapped in `ignore_errors`.

### Minor Changes

- `nomad` and `consul`: every agent option is now reachable from the
  inventory, including the `tls`, `acl`, `consul` and `vault` stanzas in
  Nomad and `tls`, `auto_encrypt`, `acl`, `dns_config`, `performance` and
  `limits` in Consul. Options that are not set are left out of the rendered
  config, so the agents' own defaults apply.
- `nomad` and `consul`: `nomad__extra_config` and `consul__extra_config` take
  an arbitrary mapping for anything the roles do not model, written as JSON
  next to the HCL.
- `nomad` and `consul`: the package version can be pinned, the rendered config
  is validated before any restart, and it is written `0640` rather than with
  the ambient umask.
- `nomad_job`, `nomad_upgrade` and `consul_service` accept an ACL token and
  client TLS settings, so they work against a secured cluster.
- All roles use FQCN module names and pass `ansible-lint`.

### Breaking Changes

- `requires_ansible` is now `>=2.15.0`. Earlier releases claimed `>=2.9` while
  already using the `ansible.builtin` FQCN, which needs 2.10 or newer.
- `community.general` is now a declared dependency; `consul_service` has always
  needed it.
