#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: nomad_agent_info
short_description: Gather information about a Nomad agent
version_added: 1.8.0
description:
  - Returns the configuration, cluster membership and runtime statistics of a
    Nomad agent, as C(nomad agent-info) does.
  - Commonly used with C(until) to wait for an agent to answer again after a
    restart or a reboot.
author:
  - René Moser (@resmo)
extends_documentation_fragment:
  - ngine_io.hashi.nomad
"""

EXAMPLES = r"""
- name: Wait for the agent to answer again
  ngine_io.hashi.nomad_agent_info:
  register: agent
  until: agent is not failed
  retries: 30
  delay: 5

- name: Show which region and datacenter the agent joined
  ansible.builtin.debug:
    msg: "{{ agent.member.Tags.region }}/{{ agent.member.Tags.dc }}"
"""

RETURN = r"""
config:
  description: Configuration the agent is running with.
  returned: success
  type: dict
  sample: {"Region": "global", "Datacenter": "dc1"}
member:
  description: Gossip membership entry of this agent.
  returned: success
  type: dict
  sample: {"Name": "server1.global", "Status": "alive"}
stats:
  description: Runtime statistics, including the C(client.node_id) of a client agent.
  returned: success
  type: dict
  sample: {"client": {"node_id": "5f1e...", "known_servers": "10.0.0.1:4647"}}
is_client:
  description: Whether the agent runs in client mode.
  returned: success
  type: bool
  sample: true
is_server:
  description: Whether the agent runs in server mode.
  returned: success
  type: bool
  sample: false
node_id:
  description: Node ID of the agent, only a client agent has one.
  returned: success
  type: str
  sample: 5f1e5b1e-0000-0000-0000-000000000000
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ngine_io.hashi.plugins.module_utils.nomad import NomadAPI, nomad_argument_spec


def main():
    module = AnsibleModule(
        argument_spec=nomad_argument_spec(),
        supports_check_mode=True,
    )

    api = NomadAPI(module)
    agent = api.agent_self() or {}
    stats = agent.get("stats") or {}
    client_stats = stats.get("client") or {}

    module.exit_json(
        changed=False,
        config=agent.get("config") or {},
        member=agent.get("member") or {},
        stats=stats,
        is_client=bool(client_stats),
        is_server=bool(stats.get("nomad")),
        node_id=client_stats.get("node_id"),
    )


if __name__ == "__main__":
    main()
