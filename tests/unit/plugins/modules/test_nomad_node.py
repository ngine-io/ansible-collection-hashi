# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from ansible_collections.ngine_io.hashi.plugins.modules.nomad_node import main
from ansible_collections.ngine_io.hashi.tests.unit.plugins.modules.utils import (
    FakeAPI,
    ModuleFailed,
    run_module,
)

NODE_ID = "5f1e5b1e-0000-0000-0000-000000000000"
NODE_PATH = "/v1/node/%s" % NODE_ID
ALLOC_PATH = "%s/allocations" % NODE_ID
HOUR_NS = 3600 * 1000 * 1000 * 1000


def node(eligibility="eligible", drain_spec=None, status="ready"):
    strategy = None
    if drain_spec is not None:
        strategy = {"DrainSpec": drain_spec}
    return {
        "ID": NODE_ID,
        "Name": "nomad-client1",
        "Status": status,
        "SchedulingEligibility": eligibility,
        "DrainStrategy": strategy,
    }


def allocs(*statuses, **kwargs):
    job_type = kwargs.get("job_type", "service")
    return [{"ClientStatus": status, "Job": {"Type": job_type}} for status in statuses]


def api(node_state, allocations=None, monkeypatch=None):
    return FakeAPI(
        {
            ("GET", "/v1/node/%s" % NODE_ID): node_state,
            ("GET", "/v1/node/%s/allocations" % NODE_ID): allocations or [],
            ("POST", "/v1/node/%s/drain" % NODE_ID): {},
            ("POST", "/v1/node/%s/eligibility" % NODE_ID): {},
        }
    ).install(monkeypatch)


BASE = {"node_id": NODE_ID, "wait": False}


def test_drain_on_an_already_drained_node_is_not_a_change(monkeypatch):
    # Nomad clears DrainStrategy when a drain finishes but leaves the node
    # ineligible. Without deriving state from the allocations, a second run
    # would start the drain all over again.
    fake = api(node(eligibility="ineligible"), [], monkeypatch)
    result = run_module(main, dict(BASE, drain=True), monkeypatch)

    assert result["changed"] is False
    assert fake.writes == []


def test_drain_on_a_running_node_starts_a_drain(monkeypatch):
    fake = api(node(), allocs("running", "running"), monkeypatch)
    result = run_module(main, dict(BASE, drain=True, drain_deadline="15m"), monkeypatch)

    assert result["changed"] is True
    drains = fake.called("POST", "/v1/node/%s/drain" % NODE_ID)
    assert len(drains) == 1
    assert drains[0]["data"]["DrainSpec"]["Deadline"] == 15 * 60 * 1000 * 1000 * 1000


def test_drain_force_sends_the_nomad_sentinel_deadline(monkeypatch):
    fake = api(node(), allocs("running"), monkeypatch)
    run_module(main, dict(BASE, drain=True, drain_force=True), monkeypatch)

    assert fake.writes[0]["data"]["DrainSpec"]["Deadline"] == -1


def test_drain_already_in_progress_with_the_same_spec_is_not_a_change(monkeypatch):
    in_progress = node(
        eligibility="ineligible",
        drain_spec={"Deadline": HOUR_NS, "IgnoreSystemJobs": False},
    )
    fake = api(in_progress, allocs("running"), monkeypatch)
    result = run_module(main, dict(BASE, drain=True), monkeypatch)

    assert result["changed"] is False
    assert fake.writes == []


def test_check_mode_reports_the_change_without_making_it(monkeypatch):
    fake = api(node(), allocs("running"), monkeypatch)
    result = run_module(main, dict(BASE, drain=True), monkeypatch, check_mode=True)

    assert result["changed"] is True
    assert fake.writes == []
    assert result["diff"]["before"]["scheduling_eligibility"] == "eligible"
    assert result["diff"]["after"]["scheduling_eligibility"] == "ineligible"


def test_terminal_allocations_do_not_keep_a_node_from_being_drained(monkeypatch):
    fake = api(node(eligibility="ineligible"), allocs("complete", "failed", "lost"), monkeypatch)
    result = run_module(main, dict(BASE, drain=True), monkeypatch)

    assert result["changed"] is False
    assert fake.writes == []


def test_system_allocations_are_ignored_when_asked_to_ignore_them(monkeypatch):
    fake = api(
        node(eligibility="ineligible"),
        allocs("running", job_type="system"),
        monkeypatch,
    )
    result = run_module(
        main, dict(BASE, drain=True, drain_ignore_system_jobs=True), monkeypatch
    )

    assert result["changed"] is False
    assert fake.writes == []


def test_cancelling_a_drain_also_marks_the_node_eligible(monkeypatch):
    draining = node(
        eligibility="ineligible",
        drain_spec={"Deadline": HOUR_NS, "IgnoreSystemJobs": False},
    )
    fake = api(draining, [], monkeypatch)
    result = run_module(main, dict(BASE, drain=False, eligible=True), monkeypatch)

    assert result["changed"] is True
    assert fake.called("POST", "/v1/node/%s/drain" % NODE_ID)[0]["data"]["DrainSpec"] is None
    eligibility = fake.called("POST", "/v1/node/%s/eligibility" % NODE_ID)
    assert eligibility[0]["data"]["Eligibility"] == "eligible"


def test_making_an_eligible_node_eligible_is_not_a_change(monkeypatch):
    fake = api(node(), [], monkeypatch)
    result = run_module(main, dict(BASE, eligible=True), monkeypatch)

    assert result["changed"] is False
    assert fake.writes == []


def test_eligible_true_with_drain_true_is_rejected(monkeypatch):
    api(node(), [], monkeypatch)
    with pytest.raises(ModuleFailed) as failure:
        run_module(main, dict(BASE, drain=True, eligible=True), monkeypatch)

    assert "ineligible" in failure.value.result["msg"]


def test_an_invalid_deadline_is_reported_rather_than_sent(monkeypatch):
    fake = api(node(), allocs("running"), monkeypatch)
    with pytest.raises(ModuleFailed) as failure:
        run_module(main, dict(BASE, drain=True, drain_deadline="soonish"), monkeypatch)

    assert "not a valid duration" in failure.value.result["msg"]
    assert fake.writes == []


def test_a_server_only_agent_gets_a_usable_error(monkeypatch):
    FakeAPI({("GET", "/v1/agent/self"): {"stats": {"nomad": {"leader": "true"}}}}).install(monkeypatch)
    with pytest.raises(ModuleFailed) as failure:
        run_module(main, {"drain": True, "wait": False}, monkeypatch)

    assert "not a client" in failure.value.result["msg"]
