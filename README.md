# Ansible Collection - ngine_io.hashi

## Install

```yaml
# file: ./requirements.yml
collections:
  - name: git@github.com:ngine-io/ansible-collection-hashi.git
    type: git
    version: master
```

## Example Configuration

NOTE: Configuring the firewalld/iptables is not part of the collection.

### Inventory

```ini
[consul:children]
nomad

[nomad:children]
nomad_servers
nomad_clients

[nomad_servers]
nomad-server[1:3]

[nomad_clients]
nomad-client[1:5]
```

### Group Vars

#### Vars for nomad
```yaml
# file: ./group_vars/nomad.yml
nomad__use_consul: true
```

#### Vars for nomad clients
```yaml
# file: ./group_vars/nomad_clients.yml
---
consul__role: client
nomad__roles:
  - client
```

#### Vars for nomad servers

```yaml
# file: ./group_vars/nomad_servers.yml
---
consul__role: server
nomad__node_class: server
nomad__roles:
  - server
  # Optional: also be a client
  # - client
```

### Playbook
```yaml
# file: ./playbooks/nomad.yml
---
- hosts: nomad_servers

- hosts: nomad_servers
  serial: 1
  roles:
    - role: ngine_io.hashi.consul
      tags: consul
    - role: ngine_io.hashi.nomad
      tags: nomad

- hosts: nomad_clients
  serial:
    - 1
    - 30%
  roles:
    # Install docker for docker workloads
    # - role: geerlingguy.docker
    #   tags: docker
    - role: ngine_io.hashi.consul
      tags: consul
    - role: ngine_io.hashi.cni
      tags: cni
    - role: ngine_io.hashi.nomad
      tags: nomad

- hosts: nomad_servers[0]
  vars:
    nomad_job__job_templates:
      - name: http-echo
        path: http-echo.nomad
      - name: traefik
        path: traefik.nomad
  roles:
    - role: ngine_io.hashi.nomad_job
      tags: nomad_job
```

### Configuration

Every option of the Nomad and Consul agents is reachable from the inventory.
The variables are named after the stanza they belong to, and are documented
inline in each role's `defaults/main.yml`:

| Role | Variables | Reference |
| --- | --- | --- |
| `consul` | `consul__*` | [roles/consul/README.md](roles/consul/README.md) |
| `nomad` | `nomad__*`, `nomad__server_*`, `nomad__client_*` | [roles/nomad/README.md](roles/nomad/README.md) |

Two rules hold throughout:

- An option that defaults to `null` is left out of the rendered config, so
  the agent's own default applies. The roles only deviate from an upstream
  default where it is called out in the role README.
- Anything a role does not model explicitly goes into `nomad__extra_config`
  or `consul__extra_config`. Both take an arbitrary mapping and are written
  as JSON next to the HCL, which the agents merge.

Both roles validate the rendered configuration before any restart handler
fires, so a mistake in the inventory fails the play rather than leaving a
crash looping agent behind.

### A hardened cluster

The defaults are open: no TLS, no ACLs, no gossip encryption. Turning that
around is roughly:

```yaml
# file: ./group_vars/nomad.yml
consul__encrypt: "{{ vault_consul_gossip_key }}"
consul__tls_enabled: true
consul__auto_encrypt_enabled: true
consul__tls_ca_content: "{{ vault_consul_ca }}"
consul__acl_enabled: true
consul__acl_token_agent: "{{ vault_consul_agent_token }}"

nomad__server_encrypt: "{{ vault_nomad_gossip_key }}"
nomad__tls_enabled: true
nomad__tls_ca_content: "{{ vault_nomad_ca }}"
nomad__tls_cert_content: "{{ vault_nomad_cert }}"
nomad__tls_key_content: "{{ vault_nomad_key }}"
nomad__acl_enabled: true
```

Each of the three steps is a rolling change of its own; see the role READMEs
for the ordering, in particular `nomad__tls_rpc_upgrade_mode` and
`consul__encrypt_verify_incoming`.

### Upgrading a Nomad cluster

```yaml
# file: ./playbooks/nomad_upgrade.yml
---
- hosts: nomad_servers
  serial: 1
  roles:
    - role: ngine_io.hashi.nomad_upgrade
      tags: nomad

- hosts: nomad_clients
  serial:
    - 1
    - 30%
  roles:
    - role: ngine_io.hashi.nomad_upgrade
      tags: nomad
```

## Modules

The collection ships modules for the operations the roles used to drive with
the `nomad` command line. They talk to the Nomad HTTP API, so they support
check mode and `--diff`, are idempotent, and can run with `delegate_to` from a
host that has no `nomad` binary.

| Module | Replaces |
| --- | --- |
| `ngine_io.hashi.nomad_node` | `nomad node eligibility`, `nomad node drain` |
| `ngine_io.hashi.nomad_job` | `nomad job run`, `nomad job plan`, `nomad job stop` |
| `ngine_io.hashi.nomad_agent_info` | `nomad agent-info` |
| `ngine_io.hashi.nomad_raft_info` | `nomad operator raft list-peers` |

Connection options fall back to the same `NOMAD_*` environment variables the
CLI uses, and can be set once for a play through the action group:

```yaml
- hosts: nomad_servers
  module_defaults:
    group/ngine_io.hashi.nomad:
      url: https://nomad.example.com:4646
      token: "{{ vault_nomad_token }}"
  tasks:
    - name: Drain a client before maintenance
      ngine_io.hashi.nomad_node:
        name: nomad-client3
        drain: true
        drain_deadline: 15m

    - name: Submit a job
      ngine_io.hashi.nomad_job:
        content: "{{ lookup('template', 'nomad_jobs/traefik.nomad.j2') }}"
```

Two notes on idempotency, since both cases are easy to get wrong:

- Nomad clears a node's drain strategy once the drain finishes but leaves the
  node ineligible. `nomad_node` therefore decides whether a node is drained
  from its eligibility and its remaining non terminal allocations, so
  re-running a drain task reports no change instead of draining again.
- `nomad_job` gets its idempotency from Nomad's plan endpoint rather than from
  comparing text, so reformatting a job file is not a change. The plan also
  reports task groups the scheduler cannot place; they are returned in
  `warnings_from_plan` and raised as a warning.
