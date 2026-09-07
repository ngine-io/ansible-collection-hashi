# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json

import pytest

from ansible_collections.ngine_io.hashi.plugins.modules.nomad_job import main
from ansible_collections.ngine_io.hashi.tests.unit.plugins.modules.utils import (
    FakeAPI,
    ModuleFailed,
    run_module,
)

JOB_ID = "traefik"
HCL = 'job "traefik" { group "web" {} }'
PARSED = {"ID": JOB_ID, "Name": JOB_ID, "TaskGroups": [{"Name": "web"}]}
REGISTERED = dict(PARSED, Stop=False, Version=3, ModifyIndex=42, Status="running")


def api(monkeypatch, current=None, diff_type="None", failed_groups=None, parsed=None):
    return FakeAPI(
        {
            ("POST", "/v1/jobs/parse"): parsed if parsed is not None else PARSED,
            ("GET", "/v1/job/%s" % JOB_ID): current,
            ("POST", "/v1/job/%s/plan" % JOB_ID): {
                "Diff": {"Type": diff_type},
                "FailedTGAllocs": failed_groups or {},
            },
            ("POST", "/v1/jobs"): {"EvalID": "eval-1"},
            ("DELETE", "/v1/job/%s" % JOB_ID): {"EvalID": "eval-2"},
        }
    ).install(monkeypatch)


def registrations(fake):
    return fake.called("POST", "/v1/jobs")


def test_an_unchanged_job_is_not_resubmitted(monkeypatch):
    # Idempotency comes from Nomad's plan, not from comparing text, so a
    # reformatted specification that means the same thing is not a change.
    fake = api(monkeypatch, current=REGISTERED, diff_type="None")
    result = run_module(main, {"content": HCL}, monkeypatch)

    assert result["changed"] is False
    assert result["diff_type"] == "None"
    assert registrations(fake) == []


def test_a_changed_job_is_registered(monkeypatch):
    fake = api(monkeypatch, current=REGISTERED, diff_type="Edited")
    result = run_module(main, {"content": HCL}, monkeypatch)

    assert result["changed"] is True
    assert result["eval_id"] == "eval-1"
    assert registrations(fake)[0]["data"]["Job"] == PARSED


def test_a_new_job_is_registered(monkeypatch):
    fake = api(monkeypatch, current=None, diff_type="Added")
    result = run_module(main, {"content": HCL}, monkeypatch)

    assert result["changed"] is True
    assert result["diff"]["before"] == {}
    assert registrations(fake)


def test_check_mode_plans_but_does_not_register(monkeypatch):
    fake = api(monkeypatch, current=REGISTERED, diff_type="Edited")
    result = run_module(main, {"content": HCL}, monkeypatch, check_mode=True)

    assert result["changed"] is True
    assert registrations(fake) == []
    assert "eval_id" not in result


def test_diff_hides_the_fields_the_servers_maintain(monkeypatch):
    api(monkeypatch, current=REGISTERED, diff_type="Edited")
    result = run_module(main, {"content": HCL}, monkeypatch)

    for volatile in ("Version", "ModifyIndex", "Status"):
        assert volatile not in result["diff"]["before"]
    assert result["diff"]["before"]["ID"] == JOB_ID


def test_a_job_that_cannot_be_placed_is_reported(monkeypatch):
    api(monkeypatch, current=None, diff_type="Added", failed_groups={"web": {}})
    result = run_module(main, {"content": HCL}, monkeypatch)

    assert result["warnings_from_plan"] == ["web"]


def test_a_name_that_contradicts_the_specification_is_rejected(monkeypatch):
    fake = api(monkeypatch, current=None, diff_type="Added")
    with pytest.raises(ModuleFailed) as failure:
        run_module(main, {"content": HCL, "name": "something-else"}, monkeypatch)

    assert "something-else" in failure.value.result["msg"]
    assert registrations(fake) == []


def test_json_specifications_skip_the_parse_endpoint(monkeypatch):
    fake = api(monkeypatch, current=REGISTERED, diff_type="None")
    result = run_module(
        main, {"content": json.dumps({"Job": PARSED}), "json": True}, monkeypatch
    )

    assert result["changed"] is False
    assert fake.called("POST", "/v1/jobs/parse") == []


def test_hcl_version_one_is_passed_through(monkeypatch):
    fake = api(monkeypatch, current=REGISTERED, diff_type="None")
    run_module(main, {"content": HCL, "hcl_version": 1}, monkeypatch)

    assert fake.called("POST", "/v1/jobs/parse")[0]["data"]["HCLv1"] is True


def test_removing_a_job_that_is_not_there_is_not_a_change(monkeypatch):
    fake = api(monkeypatch, current=None)
    result = run_module(main, {"name": JOB_ID, "state": "absent"}, monkeypatch)

    assert result["changed"] is False
    assert fake.writes == []


def test_stopping_an_already_stopped_job_is_not_a_change(monkeypatch):
    fake = api(monkeypatch, current=dict(REGISTERED, Stop=True))
    result = run_module(main, {"name": JOB_ID, "state": "absent"}, monkeypatch)

    assert result["changed"] is False
    assert fake.writes == []


def test_a_stopped_job_can_still_be_purged(monkeypatch):
    fake = api(monkeypatch, current=dict(REGISTERED, Stop=True))
    result = run_module(
        main, {"name": JOB_ID, "state": "absent", "purge": True}, monkeypatch
    )

    assert result["changed"] is True
    deletes = fake.called("DELETE", "/v1/job/%s" % JOB_ID)
    assert deletes[0]["query"] == {"purge": "true"}
    assert result["diff"]["after"] == {}


def test_stopping_a_running_job_is_a_change(monkeypatch):
    fake = api(monkeypatch, current=REGISTERED)
    result = run_module(main, {"name": JOB_ID, "state": "absent"}, monkeypatch)

    assert result["changed"] is True
    assert fake.called("DELETE", "/v1/job/%s" % JOB_ID)[0]["query"] == {"purge": "false"}
    assert result["diff"]["after"]["Stop"] is True
