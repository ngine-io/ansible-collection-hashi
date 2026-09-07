# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import re

from ansible.module_utils.basic import env_fallback
from ansible.module_utils.common.text.converters import to_native, to_text
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.module_utils.urls import fetch_url

# Fields the Nomad servers maintain themselves. They change on every write and
# would otherwise show up as spurious differences in diff output.
VOLATILE_JOB_FIELDS = (
    "CreateIndex",
    "JobModifyIndex",
    "ModifyIndex",
    "Status",
    "StatusDescription",
    "SubmitTime",
    "Version",
    "Stable",
)

DURATION_UNITS = {
    "ns": 1,
    "us": 1000,
    "ms": 1000 * 1000,
    "s": 1000 * 1000 * 1000,
    "m": 60 * 1000 * 1000 * 1000,
    "h": 3600 * 1000 * 1000 * 1000,
}

DURATION_RE = re.compile(r"(\d+)(ns|us|ms|s|m|h)")


def nomad_argument_spec():
    """Connection options shared by every module in this collection."""
    return dict(
        url=dict(
            type="str",
            default="http://127.0.0.1:4646",
            fallback=(env_fallback, ["NOMAD_ADDR"]),
        ),
        token=dict(type="str", no_log=True, fallback=(env_fallback, ["NOMAD_TOKEN"])),
        namespace=dict(type="str", fallback=(env_fallback, ["NOMAD_NAMESPACE"])),
        region=dict(type="str", fallback=(env_fallback, ["NOMAD_REGION"])),
        timeout=dict(type="int", default=30),
        validate_certs=dict(type="bool", default=True),
        client_cert=dict(type="path", fallback=(env_fallback, ["NOMAD_CLIENT_CERT"])),
        client_key=dict(
            type="path",
            no_log=False,
            fallback=(env_fallback, ["NOMAD_CLIENT_KEY"]),
        ),
        ca_path=dict(type="path", fallback=(env_fallback, ["NOMAD_CACERT"])),
    )


def parse_duration(value):
    """Turn a Go style duration such as "1h30m" into nanoseconds.

    A bare integer is read as seconds, which is what an operator writing
    "deadline: 60" almost certainly means. -1 keeps its Nomad meaning of
    "force, no deadline".
    """
    if value is None:
        return None
    text = to_text(value).strip()
    if re.match(r"^-?\d+$", text):
        seconds = int(text)
        if seconds < 0:
            return -1
        return seconds * DURATION_UNITS["s"]

    matches = DURATION_RE.findall(text)
    if not matches or "".join("%s%s" % pair for pair in matches) != text:
        raise ValueError("'%s' is not a valid duration, expected e.g. '1h', '30m' or '90s'" % value)
    return sum(int(amount) * DURATION_UNITS[unit] for amount, unit in matches)


def strip_volatile(job):
    """Remove server maintained fields so a diff shows only real changes."""
    if not isinstance(job, dict):
        return job
    return dict((key, value) for key, value in job.items() if key not in VOLATILE_JOB_FIELDS)


class NomadAPI(object):
    """Thin wrapper around the Nomad HTTP API.

    Every module talks to the API rather than shelling out to the nomad
    binary: the responses are structured, the errors carry a status code, and
    the modules work from a control node that has no nomad installed.
    """

    def __init__(self, module):
        self.module = module
        self.base_url = module.params["url"].rstrip("/")

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        token = self.module.params.get("token")
        if token:
            headers["X-Nomad-Token"] = token
        return headers

    def request(self, method, path, data=None, query=None, accept=(200,), allow_404=False):
        params = dict(query or {})
        for key in ("namespace", "region"):
            value = self.module.params.get(key)
            if value and key not in params:
                params[key] = value

        url = "%s%s" % (self.base_url, path)
        if params:
            url = "%s?%s" % (url, urlencode(params))

        body = None
        if data is not None:
            body = json.dumps(data)

        response, info = fetch_url(
            self.module,
            url,
            data=body,
            headers=self._headers(),
            method=method,
            timeout=self.module.params["timeout"],
            ca_path=self.module.params.get("ca_path"),
        )

        status = info.get("status", -1)
        if status == 404 and allow_404:
            return None

        if status not in accept:
            self.module.fail_json(
                msg="Nomad API %s %s failed with status %s: %s"
                % (method, path, status, self._error_body(response, info)),
                status=status,
                url=url,
            )

        if response is None:
            return None

        raw = to_text(response.read())
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as error:
            self.module.fail_json(
                msg="Nomad API %s %s returned a body that is not JSON: %s" % (method, path, to_native(error)),
                body=raw[:1024],
            )

    @staticmethod
    def _error_body(response, info):
        # Nomad reports errors as plain text, and fetch_url puts the body in
        # info['body'] once the status is an error.
        body = info.get("body")
        if body:
            return to_text(body).strip()
        if response is not None:
            try:
                return to_text(response.read()).strip()
            except Exception:
                pass
        return info.get("msg", "unknown error")

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, data=None, **kwargs):
        return self.request("POST", path, data=data, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def agent_self(self):
        return self.get("/v1/agent/self")

    def local_node_id(self):
        """Node ID of the agent this module is pointed at.

        Only a client agent has one, which is why this fails with an
        explanation rather than a KeyError on a server only node.
        """
        agent = self.agent_self()
        node_id = (agent.get("stats", {}).get("client", {}) or {}).get("node_id")
        if not node_id:
            self.module.fail_json(
                msg="The agent at %s is not a client, so it has no node to act on. "
                "Set 'name' or 'node_id' to address a client node explicitly." % self.base_url
            )
        return node_id
