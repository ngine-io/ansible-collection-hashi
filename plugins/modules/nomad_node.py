#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: nomad_node
short_description: Manage scheduling eligibility and drain of a Nomad client node
version_added: 1.8.0
description:
  - Sets the scheduling eligibility of a Nomad client node and starts or
    cancels a drain, replacing C(nomad node eligibility) and C(nomad node drain).
  - Idempotent. A node counts as drained when it is ineligible, has no drain in
    progress and runs no non terminal allocations, so re-running the task after
    a completed drain reports no change even though Nomad has already cleared
    the drain strategy.
author:
  - René Moser (@resmo)
options:
  name:
    description:
      - Name of the node to act on.
      - Mutually exclusive with O(node_id).
      - When neither is given, the node of the agent at O(url) is used, which
        is what C(-self) does on the command line.
    type: str
  node_id:
    description:
      - ID of the node to act on.
      - Mutually exclusive with O(name).
    type: str
  eligible:
    description:
      - Whether the node is eligible for scheduling.
      - Cannot be V(true) together with O(drain=true), because Nomad marks a
        draining node ineligible by definition.
    type: bool
  drain:
    description:
      - Whether the node is drained.
      - V(true) starts a drain and, unless O(wait=false), waits for it to
        finish. V(false) cancels a drain that is in progress.
    type: bool
  drain_deadline:
    description:
      - How long Nomad may take to migrate the allocations before it forces
        them off the node.
      - Accepts a Go style duration such as V(1h) or V(90s). A bare integer is
        read as seconds.
      - Ignored when O(drain_force=true).
    type: str
    default: 1h
  drain_force:
    description:
      - Drop the allocations immediately instead of migrating them within
        O(drain_deadline).
    type: bool
    default: false
  drain_ignore_system_jobs:
    description:
      - Leave allocations of system and sysbatch jobs running.
      - They are also left out of the check for whether the node is drained.
    type: bool
    default: false
  wait:
    description:
      - Whether to wait for a started drain to complete.
    type: bool
    default: true
  wait_timeout:
    description:
      - How long in seconds to wait for a drain to complete.
      - Should be at least as long as O(drain_deadline).
    type: int
    default: 3600
extends_documentation_fragment:
  - ngine_io.hashi.nomad
"""

EXAMPLES = r"""
- name: Drain the local node before a reboot
  ngine_io.hashi.nomad_node:
    drain: true
    drain_deadline: 15m

- name: Make the local node eligible again
  ngine_io.hashi.nomad_node:
    drain: false
    eligible: true

- name: Take a named node out of scheduling without moving its allocations
  ngine_io.hashi.nomad_node:
    name: nomad-client3
    eligible: false

- name: Drain a node from the control host
  ngine_io.hashi.nomad_node:
    url: https://nomad.example.com:4646
    token: "{{ vault_nomad_token }}"
    name: nomad-client3
    drain: true
  delegate_to: localhost
"""

RETURN = r"""
node_id:
  description: ID of the node that was acted on.
  returned: success
  type: str
  sample: 5f1e5b1e-0000-0000-0000-000000000000
name:
  description: Name of the node that was acted on.
  returned: success
  type: str
  sample: nomad-client3
scheduling_eligibility:
  description: Scheduling eligibility after the run.
  returned: success
  type: str
  sample: eligible
draining:
  description: Whether a drain is in progress after the run.
  returned: success
  type: bool
  sample: false
allocations:
  description: Number of non terminal allocations left on the node.
  returned: success
  type: int
  sample: 0
status:
  description: Node status as reported by Nomad.
  returned: success
  type: str
  sample: ready
"""

import time

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ngine_io.hashi.plugins.module_utils.nomad import (
    NomadAPI,
    nomad_argument_spec,
    parse_duration,
)

ACTIVE_CLIENT_STATUS = ("pending", "running")
SYSTEM_JOB_TYPES = ("system", "sysbatch")


class NomadNode(object):

    def __init__(self, module):
        self.module = module
        self.api = NomadAPI(module)
        self.node_id = self._resolve_node_id()

    def _resolve_node_id(self):
        node_id = self.module.params["node_id"]
        if node_id:
            return node_id

        name = self.module.params["name"]
        if not name:
            return self.api.local_node_id()

        for node in self.api.get("/v1/nodes") or []:
            if node.get("Name") == name:
                return node.get("ID")
        self.module.fail_json(msg="No Nomad node named '%s' was found." % name)

    def get_node(self):
        node = self.api.get("/v1/node/%s" % self.node_id, allow_404=True)
        if node is None:
            self.module.fail_json(msg="Nomad node '%s' was not found." % self.node_id)
        return node

    def active_allocations(self):
        """Non terminal allocations, which is what "is it drained" comes down to."""
        ignore_system = self.module.params["drain_ignore_system_jobs"]
        count = 0
        for alloc in self.api.get("/v1/node/%s/allocations" % self.node_id) or []:
            if alloc.get("ClientStatus") not in ACTIVE_CLIENT_STATUS:
                continue
            if ignore_system and (alloc.get("Job") or {}).get("Type") in SYSTEM_JOB_TYPES:
                continue
            count += 1
        return count

    def desired_drain_spec(self):
        if self.module.params["drain_force"]:
            deadline = -1
        else:
            try:
                deadline = parse_duration(self.module.params["drain_deadline"])
            except ValueError as error:
                self.module.fail_json(msg=str(error))
        return {
            "Deadline": deadline,
            "IgnoreSystemJobs": self.module.params["drain_ignore_system_jobs"],
        }

    @staticmethod
    def current_drain_spec(node):
        strategy = node.get("DrainStrategy")
        if not strategy:
            return None
        spec = strategy.get("DrainSpec") or {}
        return {
            "Deadline": spec.get("Deadline"),
            "IgnoreSystemJobs": bool(spec.get("IgnoreSystemJobs")),
        }

    def state_of(self, node, allocations):
        return {
            "scheduling_eligibility": node.get("SchedulingEligibility"),
            "draining": self.current_drain_spec(node) is not None,
            "drain_spec": self.current_drain_spec(node),
            "allocations": allocations,
        }

    def set_drain(self, enable):
        data = {"NodeID": self.node_id, "MarkEligible": False}
        data["DrainSpec"] = self.desired_drain_spec() if enable else None
        self.api.post("/v1/node/%s/drain" % self.node_id, data=data)

    def set_eligibility(self, eligible):
        self.api.post(
            "/v1/node/%s/eligibility" % self.node_id,
            data={
                "NodeID": self.node_id,
                "Eligibility": "eligible" if eligible else "ineligible",
            },
        )

    def wait_for_drain(self):
        deadline = time.time() + self.module.params["wait_timeout"]
        while time.time() < deadline:
            node = self.get_node()
            if self.current_drain_spec(node) is None and self.active_allocations() == 0:
                return
            time.sleep(5)
        self.module.fail_json(
            msg="Timed out after %ss waiting for node '%s' to finish draining."
            % (self.module.params["wait_timeout"], self.node_id)
        )

    def run(self):
        params = self.module.params
        node = self.get_node()
        allocations = self.active_allocations()
        before = self.state_of(node, allocations)

        drain = params["drain"]
        eligible = params["eligible"]

        after = dict(before)
        drain_change = False
        eligibility_change = False
        drain_in_progress = False

        if drain is True:
            # Nomad clears DrainStrategy once a drain finishes but leaves the
            # node ineligible, so "already drained" has to be derived from the
            # node state rather than from the drain strategy alone.
            already_drained = (
                before["scheduling_eligibility"] == "ineligible"
                and not before["draining"]
                and allocations == 0
            )
            drain_in_progress = before["drain_spec"] == self.desired_drain_spec()
            drain_change = not already_drained and not drain_in_progress
            after.update(
                {
                    "scheduling_eligibility": "ineligible",
                    "draining": False,
                    "drain_spec": None,
                    "allocations": 0,
                }
            )
        elif drain is False and before["draining"]:
            drain_change = True
            after.update({"draining": False, "drain_spec": None})

        if eligible is not None:
            wanted = "eligible" if eligible else "ineligible"
            if after["scheduling_eligibility"] != wanted:
                eligibility_change = True
                after["scheduling_eligibility"] = wanted

        changed = drain_change or eligibility_change
        result = {
            "changed": changed,
            "node_id": self.node_id,
            "name": node.get("Name"),
            "status": node.get("Status"),
            "diff": {"before": before, "after": after},
        }

        if self.module.check_mode:
            result.update(
                {
                    "scheduling_eligibility": after["scheduling_eligibility"],
                    "draining": after["draining"],
                    "allocations": after["allocations"],
                }
            )
            self.module.exit_json(**result)

        if drain_change:
            self.set_drain(drain)
        if drain and params["wait"] and (drain_change or drain_in_progress):
            # A drain someone else started with the same spec is not a change,
            # but the node is still not drained until it finishes.
            self.wait_for_drain()
        if eligibility_change:
            self.set_eligibility(eligible)

        node = self.get_node()
        allocations = self.active_allocations()
        result.update(
            {
                "scheduling_eligibility": node.get("SchedulingEligibility"),
                "draining": self.current_drain_spec(node) is not None,
                "allocations": allocations,
                "status": node.get("Status"),
            }
        )
        self.module.exit_json(**result)


def main():
    argument_spec = nomad_argument_spec()
    argument_spec.update(
        name=dict(type="str"),
        node_id=dict(type="str"),
        eligible=dict(type="bool"),
        drain=dict(type="bool"),
        drain_deadline=dict(type="str", default="1h"),
        drain_force=dict(type="bool", default=False),
        drain_ignore_system_jobs=dict(type="bool", default=False),
        wait=dict(type="bool", default=True),
        wait_timeout=dict(type="int", default=3600),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        mutually_exclusive=[("name", "node_id")],
        required_one_of=[("eligible", "drain")],
        supports_check_mode=True,
    )

    if module.params["drain"] and module.params["eligible"]:
        module.fail_json(
            msg="'eligible: true' cannot be combined with 'drain: true': Nomad marks a draining node ineligible."
        )

    NomadNode(module).run()


if __name__ == "__main__":
    main()
