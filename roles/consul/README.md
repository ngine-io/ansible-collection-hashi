# ngine_io.hashi.consul

Installs and configures a Consul agent as a server or a client, selected with
`consul__role`.

## How the configuration is built

Everything is rendered into a single `consul.hcl` in `consul__config_path`
(`/etc/consul.d`). Server only stanzas — `bootstrap_expect`, the Raft
settings, `autopilot` — are emitted only when `consul__role` is `server`.

Variables are named after the stanza they belong to:
`consul__dns_only_passing` is `dns_config { only_passing }`,
`consul__acl_default_policy` is `acl { default_policy }`. Optional options
default to `null` and are then left out entirely, so Consul's own default
applies.

The config holds the gossip key and the ACL tokens, so it is written 0640
owned by the `consul` user the packaged unit runs as. `consul validate` runs
before any restart handler fires.

## Servers and clients differ in more than one flag

Two defaults are derived from `consul__role` rather than being fixed:

- `leave_on_terminate` is true on clients and false on servers. A server that
  leaves gracefully is removed from the Raft peer set, so a restart would
  otherwise shrink the cluster on every run.
- `skip_leave_on_interrupt` is the other way round for the same reason.

## Securing a cluster

```yaml
# 1. Gossip encryption. To roll this onto a running cluster, set
#    consul__encrypt_verify_incoming and _outgoing to false first.
consul__encrypt: "{{ vault_consul_gossip_key }}"

# 2. TLS. With auto_encrypt only the servers need certificate material,
#    the clients get theirs from the servers.
consul__tls_enabled: true
consul__auto_encrypt_enabled: true
consul__tls_ca_content: "{{ vault_consul_ca }}"
consul__tls_cert_content: "{{ vault_consul_cert }}"   # servers only
consul__tls_key_content: "{{ vault_consul_key }}"     # servers only

# 3. ACLs, after the tokens have been bootstrapped and distributed.
consul__acl_enabled: true
consul__acl_token_agent: "{{ vault_consul_agent_token }}"
```

`enable_script_checks` stays off. The non local form lets anyone with API
write run commands on the agent host; use
`consul__enable_local_script_checks` with checks defined in the agent config.

## Services in the agent config

`consul_service` registers through the API and is the right default. For a
check that has to exist before anything talks to the agent, or a service that
must survive a `data_dir` wipe, use the config file form:

```yaml
consul__services:
  - name: node-exporter
    port: 9100
    checks:
      - id: node-exporter-http
        http: "http://localhost:9100/metrics"
        interval: 30s
```

## Anything the role does not model

`consul__extra_config` takes an arbitrary mapping and is written as JSON,
which Consul merges with the HCL. `consul__extra_config_files` takes verbatim
HCL keyed by file name.
