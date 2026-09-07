# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from ansible_collections.ngine_io.hashi.plugins.module_utils.nomad import (
    parse_duration,
    strip_volatile,
)

SECOND = 1000 * 1000 * 1000


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1h", 3600 * SECOND),
        ("30m", 1800 * SECOND),
        ("90s", 90 * SECOND),
        ("1h30m", 5400 * SECOND),
        ("500ms", 500 * 1000 * 1000),
        # A bare integer is seconds, which is what "deadline: 60" means to
        # anyone who is not thinking in nanoseconds.
        (60, 60 * SECOND),
        ("60", 60 * SECOND),
        # Nomad's own encoding of a forced drain.
        (-1, -1),
        (None, None),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["bogus", "1x", "1h30", "", "h"])
def test_parse_duration_rejects_nonsense(value):
    with pytest.raises(ValueError):
        parse_duration(value)


def test_strip_volatile_removes_server_maintained_fields():
    job = {
        "ID": "traefik",
        "TaskGroups": [],
        "CreateIndex": 5,
        "ModifyIndex": 9,
        "JobModifyIndex": 9,
        "Version": 3,
        "Status": "running",
        "SubmitTime": 1234567890,
        "Stable": True,
    }
    assert strip_volatile(job) == {"ID": "traefik", "TaskGroups": []}


def test_strip_volatile_passes_through_non_dicts():
    assert strip_volatile(None) is None
    assert strip_volatile("nope") == "nope"
