# -*- coding: utf-8 -*-
# Copyright (c) 2026, René Moser <mail@renemoser.net>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type


class ModuleDocFragment(object):

    DOCUMENTATION = r"""
options:
  url:
    description:
      - Base URL of the Nomad HTTP API.
    type: str
    default: http://127.0.0.1:4646
  token:
    description:
      - ACL token used to authenticate against the API.
    type: str
  namespace:
    description:
      - Nomad namespace to operate in.
      - Defaults to the namespace the agent applies, usually C(default).
    type: str
  region:
    description:
      - Nomad region to operate in.
      - Defaults to the region the agent applies.
    type: str
  timeout:
    description:
      - Timeout in seconds for a single API request.
    type: int
    default: 30
  validate_certs:
    description:
      - Whether the TLS certificate of the API is verified.
      - Never set this to V(false) outside a lab.
    type: bool
    default: true
  client_cert:
    description:
      - Path to a client certificate, for an API running with
        C(verify_https_client) enabled.
    type: path
  client_key:
    description:
      - Path to the private key belonging to O(client_cert).
    type: path
  ca_path:
    description:
      - Path to a CA bundle used to verify the API certificate.
    type: path
notes:
  - All connection options fall back to the environment variables the C(nomad)
    CLI uses, that is E(NOMAD_ADDR), E(NOMAD_TOKEN), E(NOMAD_NAMESPACE),
    E(NOMAD_REGION), E(NOMAD_CLIENT_CERT), E(NOMAD_CLIENT_KEY) and
    E(NOMAD_CACERT).
  - The modules talk to the HTTP API, so they do not need the C(nomad) binary
    on the target and can run with C(delegate_to) from anywhere that can reach
    the API.
"""
