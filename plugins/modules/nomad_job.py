#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: nomad_job
short_description: Submit and remove Nomad jobs
version_added: 1.8.0
description:
  - Registers a job from an HCL or JSON specification, or stops and optionally
    purges one, replacing C(nomad job run), C(nomad job plan) and
    C(nomad job stop).
  - Idempotency comes from Nomad's own plan endpoint rather than from comparing
    text, so a reformatted specification that means the same thing does not
    report a change.
author:
  - René Moser (@resmo)
options:
  name:
    description:
      - ID of the job.
      - Required when O(state=absent). For O(state=present) it is taken from
        the specification, and if given it must match, which catches a
        specification that was copied without changing its ID.
    type: str
  content:
    description:
      - The job specification itself.
      - Mutually exclusive with O(src).
    type: str
  src:
    description:
      - Path to a file on the target host holding the job specification.
      - Mutually exclusive with O(content).
    type: path
  state:
    description:
      - V(present) registers the job, V(absent) stops it.
    type: str
    choices: [present, absent]
    default: present
  purge:
    description:
      - With O(state=absent), remove the job from the catalog instead of
        leaving it stopped.
    type: bool
    default: false
  hcl_version:
    description:
      - Which HCL dialect O(content) or O(src) is written in.
      - Ignored when O(json=true).
    type: int
    choices: [1, 2]
    default: 2
  json:
    description:
      - Whether the specification is JSON rather than HCL.
      - A JSON specification may be either a bare job object or one wrapped in
        a C(Job) key, as the API returns it.
    type: bool
    default: false
extends_documentation_fragment:
  - ngine_io.hashi.nomad
"""

EXAMPLES = r"""
- name: Submit a job rendered from a template
  ngine_io.hashi.nomad_job:
    content: "{{ lookup('template', 'nomad_jobs/traefik.nomad.j2') }}"

- name: Submit a job from a file on the target
  ngine_io.hashi.nomad_job:
    src: /etc/nomad.d/jobs/http-echo.nomad

- name: Stop a job but keep it in the catalog
  ngine_io.hashi.nomad_job:
    name: http-echo
    state: absent

- name: Remove a job for good
  ngine_io.hashi.nomad_job:
    name: http-echo
    state: absent
    purge: true

- name: Show what would change without touching the cluster
  ngine_io.hashi.nomad_job:
    content: "{{ lookup('template', 'nomad_jobs/traefik.nomad.j2') }}"
  check_mode: true
  diff: true
"""

RETURN = r"""
job_id:
  description: ID of the job that was acted on.
  returned: success
  type: str
  sample: traefik
diff_type:
  description:
    - What Nomad's plan reported, one of V(None), V(Added) or V(Edited).
    - Only returned for O(state=present).
  returned: success and O(state=present)
  type: str
  sample: Edited
eval_id:
  description: ID of the evaluation the change created.
  returned: changed
  type: str
  sample: 9f4e1c0e-0000-0000-0000-000000000000
warnings_from_plan:
  description:
    - Task groups the scheduler could not place when planning the job.
    - A non empty list means the job was accepted but will not run as it stands.
  returned: success and O(state=present)
  type: list
  elements: str
  sample: ["web"]
"""

import json as json_lib

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.ngine_io.hashi.plugins.module_utils.nomad import (
    NomadAPI,
    nomad_argument_spec,
    strip_volatile,
)


class NomadJob(object):

    def __init__(self, module):
        self.module = module
        self.api = NomadAPI(module)

    def read_spec(self):
        if self.module.params["content"] is not None:
            return self.module.params["content"]
        src = self.module.params["src"]
        try:
            with open(src, "rb") as handle:
                return handle.read().decode("utf-8")
        except (IOError, OSError, UnicodeDecodeError) as error:
            self.module.fail_json(msg="Could not read job specification '%s': %s" % (src, error))

    def parse_spec(self, spec):
        """Turn a specification into the canonical job object Nomad works with."""
        if self.module.params["json"]:
            try:
                parsed = json_lib.loads(spec)
            except ValueError as error:
                self.module.fail_json(msg="The job specification is not valid JSON: %s" % error)
            return parsed.get("Job", parsed)

        return self.api.post(
            "/v1/jobs/parse",
            data={
                "JobHCL": spec,
                "HCLv1": self.module.params["hcl_version"] == 1,
                "Canonicalize": True,
            },
        )

    def get_job(self, job_id):
        return self.api.get("/v1/job/%s" % job_id, allow_404=True)

    def present(self):
        job = self.parse_spec(self.read_spec())
        job_id = job.get("ID")
        if not job_id:
            self.module.fail_json(msg="The job specification has no ID.")

        name = self.module.params["name"]
        if name and name != job_id:
            self.module.fail_json(
                msg="The job specification declares ID '%s' but 'name' is '%s'." % (job_id, name)
            )

        current = self.get_job(job_id)

        # The plan endpoint is what the cluster itself would do with this
        # submission, which makes it a better source of truth for "would this
        # change anything" than any comparison done here.
        plan = self.api.post("/v1/job/%s/plan" % job_id, data={"Job": job, "Diff": True}) or {}
        diff_type = (plan.get("Diff") or {}).get("Type", "None")
        changed = diff_type != "None"

        result = {
            "changed": changed,
            "job_id": job_id,
            "diff_type": diff_type,
            "warnings_from_plan": sorted((plan.get("FailedTGAllocs") or {}).keys()),
            "diff": {
                "before": strip_volatile(current or {}),
                "after": strip_volatile(job),
            },
        }

        if result["warnings_from_plan"]:
            self.module.warn(
                "Nomad could not place task group(s) %s when planning job '%s'."
                % (", ".join(result["warnings_from_plan"]), job_id)
            )

        if changed and not self.module.check_mode:
            registered = self.api.post("/v1/jobs", data={"Job": job}) or {}
            result["eval_id"] = registered.get("EvalID")

        self.module.exit_json(**result)

    def absent(self):
        job_id = self.module.params["name"]
        current = self.get_job(job_id)

        purge = self.module.params["purge"]
        # A job that is already stopped only needs another call when it also
        # has to disappear from the catalog.
        changed = current is not None and (purge or not current.get("Stop"))

        before = strip_volatile(current or {})
        if current is None or purge:
            after = {}
        else:
            after = dict(before, Stop=True)

        result = {
            "changed": changed,
            "job_id": job_id,
            "diff": {"before": before, "after": after},
        }

        if changed and not self.module.check_mode:
            stopped = self.api.delete(
                "/v1/job/%s" % job_id,
                query={"purge": "true" if purge else "false"},
            ) or {}
            result["eval_id"] = stopped.get("EvalID")

        self.module.exit_json(**result)

    def run(self):
        if self.module.params["state"] == "present":
            self.present()
        else:
            self.absent()


def main():
    argument_spec = nomad_argument_spec()
    argument_spec.update(
        name=dict(type="str"),
        content=dict(type="str"),
        src=dict(type="path"),
        state=dict(type="str", choices=["present", "absent"], default="present"),
        purge=dict(type="bool", default=False),
        hcl_version=dict(type="int", choices=[1, 2], default=2),
        json=dict(type="bool", default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        mutually_exclusive=[("content", "src")],
        required_if=[
            ("state", "present", ("content", "src"), True),
            ("state", "absent", ("name",)),
        ],
        supports_check_mode=True,
    )

    NomadJob(module).run()


if __name__ == "__main__":
    main()
