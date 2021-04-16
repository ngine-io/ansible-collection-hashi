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
  serial: 1
  roles:
    - role: ngine_io.hashi.consul
      tags: consul
    - role: ngine_io.hashi.nomad
      tags: nomad

- hosts: nomad_clients
  roles:
    # Install docker for docker workloads
    # - role: geerlingguy.docker
    #   tags: docker
    - role: ngine_io.hashi.consul
      tags: consul
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
