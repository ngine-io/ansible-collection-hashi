# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
from contextlib import contextmanager

from ansible.module_utils import basic
from ansible.module_utils.common.text.converters import to_bytes

from ansible_collections.ngine_io.hashi.plugins.module_utils.nomad import NomadAPI


class ModuleExit(Exception):
    """Stands in for the SystemExit a module raises when it finishes."""

    def __init__(self, result):
        super(ModuleExit, self).__init__(result.get("msg", "module exited"))
        self.result = result


class ModuleFailed(ModuleExit):
    pass


@contextmanager
def set_module_args(args):
    """Feed arguments to the module under test.

    ansible-core 2.19 added a supported helper for this; fall back to the long
    standing private attribute on older releases.
    """
    try:
        from ansible.module_utils.testing import patch_module_args
    except ImportError:
        patch_module_args = None

    if patch_module_args is not None:
        with patch_module_args(args):
            yield
        return

    previous = getattr(basic, "_ANSIBLE_ARGS", None)
    basic._ANSIBLE_ARGS = to_bytes(json.dumps({"ANSIBLE_MODULE_ARGS": args}))
    try:
        yield
    finally:
        basic._ANSIBLE_ARGS = previous


class FakeAPI(object):
    """Serves canned API responses and records what the module asked for.

    Keys are (method, path); a path of None matches any path for that method.
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def install(self, monkeypatch):
        fake = self

        def request(_self, method, path, data=None, query=None, accept=(200,), allow_404=False):
            fake.calls.append({"method": method, "path": path, "data": data, "query": query})
            for key in ((method, path), (method, None)):
                if key in fake.responses:
                    value = fake.responses[key]
                    return value(fake) if callable(value) else value
            if allow_404:
                return None
            raise AssertionError("unexpected API call: %s %s" % (method, path))

        monkeypatch.setattr(NomadAPI, "request", request)
        return self

    def called(self, method, path=None):
        return [
            call
            for call in self.calls
            if call["method"] == method and (path is None or call["path"] == path)
        ]

    @property
    def writes(self):
        return [call for call in self.calls if call["method"] in ("POST", "PUT", "DELETE")]


def run_module(main, args, monkeypatch, check_mode=False):
    """Run a module's main() and return whatever it exited with."""

    def exit_json(_self, **kwargs):
        kwargs.setdefault("changed", False)
        raise ModuleExit(kwargs)

    def fail_json(_self, **kwargs):
        kwargs["failed"] = True
        raise ModuleFailed(kwargs)

    monkeypatch.setattr(basic.AnsibleModule, "exit_json", exit_json)
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail_json)

    full_args = dict(args)
    full_args["_ansible_check_mode"] = check_mode
    full_args["_ansible_diff"] = True

    with set_module_args(full_args):
        try:
            main()
        # ModuleFailed subclasses ModuleExit, so it has to be let through
        # first or a failing module would look like a successful one.
        except ModuleFailed:
            raise
        except ModuleExit as exited:
            return exited.result
    raise AssertionError("module did not exit")
