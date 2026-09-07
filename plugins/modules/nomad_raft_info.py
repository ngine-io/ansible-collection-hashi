#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: nomad_raft_info
short_description: Gather information about the Nomad Raft peer set
version_added: 1.8.0
description:
  - Returns the Raft configuration of the region, as
    C(nomad operator raft list-peers) does.
  - Used to wait for a server to rejoin the peer set before a rolling upgrade
    moves on to the next one.
author:
  - René Moser (@resmo)
extends_documentation_fragment:
  - ngine_io.hashi.nomad
"""

EXAMPLES = r"""
- name: Wait for all three servers to be voters again
  ngine_io.hashi.nomad_raft_info:
  register: raft
  until: raft is not failed and raft.voters | length >= 3
  retries: 30
  delay: 10

- name: Fail if the region has no leader
  ansible.builtin.assert:
    that: raft.leader is not none
    fail_msg: The region has no Raft leader.
"""

RETURN = r"""
servers:
  description: Every server in the Raft configuration.
  returned: success
  type: list
  elements: dict
  sample:
    - {"ID": "10.0.0.1:4647", "Node": "server1.global", "Address": "10.0.0.1:4647", "Leader": true, "Voter": true}
voters:
  description: Addresses of the servers that are voting members.
  returned: success
  type: list
  elements: str
  sample: ["10.0.0.1:4647", "10.0.0.2:4647", "10.0.0.3:4647"]
leader:
  description: Address of the current leader, V(none) while an election is in progress.
  returned: success
  type: str
  sample: 10.0.0.1:4647
index:
  description: Raft index the configuration was read at.
  returned: success
  type: int
  sample: 42
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ngine_io.hashi.plugins.module_utils.nomad import NomadAPI, nomad_argument_spec


def main():
    module = AnsibleModule(
        argument_spec=nomad_argument_spec(),
        supports_check_mode=True,
    )

    api = NomadAPI(module)
    configuration = api.get("/v1/operator/raft/configuration") or {}
    servers = configuration.get("Servers") or []

    leader = None
    for server in servers:
        if server.get("Leader"):
            leader = server.get("Address")
            break

    module.exit_json(
        changed=False,
        servers=servers,
        voters=[server.get("Address") for server in servers if server.get("Voter")],
        leader=leader,
        index=configuration.get("Index"),
    )


if __name__ == "__main__":
    main()
