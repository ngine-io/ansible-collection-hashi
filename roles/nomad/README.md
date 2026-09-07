# ngine_io.hashi.nomad

Installs and configures a Nomad agent as a server, a client or both.

## How the configuration is built

The role renders three files into `nomad__config_path` (`/etc/nomad.d`):

| File | Content | Rendered when |
| --- | --- | --- |
| `nomad.hcl` | agent level options, `telemetry`, `ui`, `limits`, and the `consul`, `vault`, `acl` and `tls` stanzas | always |
| `server.hcl` | the `server` stanza and `autopilot` | `server` in `nomad__roles` |
| `client.hcl` | the `client` stanza and the task driver `plugin` blocks | `client` in `nomad__roles` |

Every option is a variable named after the stanza it belongs to, so
`nomad__server_heartbeat_grace` is `server { heartbeat_grace }` and
`nomad__client_gc_disk_usage_threshold` is `client { gc_disk_usage_threshold }`.
See `defaults/main.yml`, which is ordered the same way as the rendered files.

Optional options default to `null` and are then left out of the rendered
config, so Nomad's own default applies. Setting a variable is the only way to
change behaviour; upgrading the role does not.

`nomad config validate` runs over the whole config directory before any
restart handler fires, so a mistake fails the play instead of leaving a
crash looping agent behind.

## Anything the role does not model

`nomad__extra_config` takes an arbitrary mapping and is written as JSON.
Nomad merges `.hcl` and `.json` from its config directory, and the file is
named so it is merged last:

```yaml
nomad__extra_config:
  audit:
    enabled: true
    sink:
      file:
        type: file
        format: json
        path: /var/log/nomad/audit.log
```

`nomad__extra_config_files` takes verbatim HCL keyed by file name for the
cases where HCL specific syntax matters.

## Securing a cluster

The defaults are deliberately open, matching what the role did before these
options existed. A hardened cluster needs, in this order:

```yaml
# 1. Encrypt gossip between servers
nomad__server_encrypt: "{{ vault_nomad_gossip_key }}"

# 2. Mutual TLS for HTTP and RPC. Roll this out with
#    nomad__tls_rpc_upgrade_mode true on the first pass.
nomad__tls_enabled: true
nomad__tls_ca_content: "{{ vault_nomad_ca }}"
nomad__tls_cert_content: "{{ vault_nomad_cert }}"
nomad__tls_key_content: "{{ vault_nomad_key }}"

# 3. ACLs. Bootstrap the management token first, this only enforces.
nomad__acl_enabled: true
```

## Draining on reboot

`nomad__client_drain_on_shutdown_enabled` makes a client hand its allocations
over when the agent stops, instead of letting them fail over once the
heartbeat expires. It only works if systemd waits long enough:

```yaml
nomad__client_drain_on_shutdown_enabled: true
nomad__client_drain_on_shutdown_deadline: 5m
nomad__systemd_timeout_stop_sec: 330
```

## Task driver plugins

`docker` and `raw_exec` are always rendered, `exec` and `java` when enabled.
Any other driver goes through `nomad__client_plugins`, whose value is turned
into the plugin's `config` block, nested mappings included:

```yaml
nomad__client_plugins:
  nomad-driver-podman:
    socket_path: "unix:///run/podman/podman.sock"
    volumes:
      enabled: true
```
