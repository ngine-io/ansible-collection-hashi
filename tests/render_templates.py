"""Render every role template and check the result parses as HCL.

This collection is almost entirely Jinja, and a broken template only shows up
when a play reaches the host it applies to. Rendering the templates against
their own defaults, plus a set of overrides that switches on the optional
stanzas, catches undefined variables, bad filters and unbalanced braces in CI
instead.

Usage: python3 tests/render_templates.py
"""

import io
import json
import os
import re
import sys

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

try:
    import hcl2
except ImportError:  # pragma: no cover - the HCL check is optional
    hcl2 = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FACTS = {
    "ansible_managed": "Ansible managed",
    "ansible_os_family": "Debian",
    "ansible_distribution_release": "bookworm",
    "ansible_architecture": "x86_64",
    "ansible_default_ipv4": {"address": "10.0.0.10"},
    "inventory_hostname_short": "node1",
    "hashi_common__datacenter": "dc1",
    "groups": {"nomad_servers": ["s1", "s2", "s3"], "consul": ["s1", "s2", "s3"]},
    "hostvars": {
        host: {"ansible_default_ipv4": {"address": "10.0.0.%d" % (index + 1)}}
        for index, host in enumerate(["s1", "s2", "s3"])
    },
}

# Inventory derived lists other defaults refer to, resolved first.
SERVER_LISTS = ("nomad__servers", "consul__servers")

CASES = [
    ("nomad", "nomad.hcl.j2", {}),
    (
        "nomad",
        "nomad.hcl.j2",
        {
            "nomad__use_consul": True,
            "nomad__use_vault": True,
            "nomad__acl_enabled": True,
            "nomad__tls_enabled": True,
            "nomad__address_http": "127.0.0.1",
            "nomad__limits_rpc_max_conns_per_client": 100,
            "nomad__http_api_response_headers": {"Access-Control-Allow-Origin": "*"},
            "nomad__ui_label_text": "prod",
            "nomad__telemetry_statsd_address": "127.0.0.1:8125",
            "nomad__telemetry_prefix_filter": ["-nomad.raft"],
            "nomad__vault_jwt_auth_backend_path": "jwt-nomad",
        },
    ),
    ("nomad", "server.hcl.j2", {}),
    (
        "nomad",
        "server.hcl.j2",
        {
            "nomad__server_encrypt": "gossip-key",
            "nomad__server_raft_protocol": 3,
            "nomad__server_heartbeat_grace": "30s",
            "nomad__server_plan_rejection_tracker_enabled": True,
            "nomad__server_job_gc_interval": "5m",
            "nomad__server_enabled_schedulers": ["service", "batch"],
            "nomad__autopilot_min_quorum": 3,
        },
    ),
    ("nomad", "client.hcl.j2", {}),
    (
        "nomad",
        "client.hcl.j2",
        {
            "nomad__client_meta": {"rack": "r1"},
            "nomad__client_options": {"driver.allowlist": "docker"},
            "nomad__client_chroot_env": {"/bin": "/bin"},
            "nomad__client_host_volumes": [{"name": "mysql", "path": "/mysql"}],
            "nomad__client_host_networks": [{"name": "public", "cidr": "203.0.113.0/24"}],
            "nomad__client_drain_on_shutdown_enabled": True,
            "nomad__client_template_function_denylist": ["plugin"],
            "nomad__client_artifact_git_timeout": "30m",
            "nomad__client_plugin_exec_enabled": True,
            "nomad__client_plugin_java_enabled": True,
            "nomad__client_plugin_docker_extra_config": {"container_exists_attempts": 5},
            "nomad__client_plugins": {
                "nomad-driver-podman": {
                    "socket_path": "unix:///run/podman/podman.sock",
                    "volumes": {"enabled": True},
                }
            },
        },
    ),
    ("consul", "consul.hcl.j2", {}),
    (
        "consul",
        "consul.hcl.j2",
        {
            "consul__role": "client",
            "consul__tls_enabled": True,
            "consul__auto_encrypt_enabled": True,
            "consul__acl_enabled": True,
            "consul__encrypt": "gossip-key",
            "consul__node_meta": {"zone": "a"},
            "consul__dns_service_ttl": {"*": "30s"},
            "consul__performance_raft_multiplier": 1,
            "consul__limits_rpc_rate": 100,
            "consul__address_http": "127.0.0.1",
            "consul__retry_join_wan": ["10.1.0.1"],
            "consul__ui_metrics_provider": "prometheus",
            "consul__ui_metrics_proxy_base_url": "http://prometheus:9090",
            "consul__connect_ca_provider": "vault",
            "consul__connect_ca_config": {"address": "https://vault:8200"},
            "consul__telemetry_dogstatsd_addr": "127.0.0.1:8125",
        },
    ),
    (
        "consul",
        "consul.hcl.j2",
        {
            "consul__acl_enabled": True,
            "consul__acl_token_agent": "token",
            "consul__license_path": "/etc/consul.d/consul.hclic",
            "consul__autopilot_min_quorum": 3,
            "consul__raft_snapshot_threshold": 32768,
        },
    ),
]

# Non HCL templates, rendered but not parsed.
RENDER_ONLY = [
    ("nomad", "nomad_systemd.service.j2", {}),
    ("nomad", "nomad_systemd.service.j2", {"nomad__systemd_timeout_stop_sec": 330}),
    ("consul", "consul_systemd_override.conf.j2", {}),
]


def role_environment(role, overrides):
    variables = {}
    for name in ("defaults/main.yml", "vars/main.yml"):
        path = os.path.join(ROOT, "roles", role, name)
        if os.path.exists(path):
            variables.update(yaml.safe_load(open(path)) or {})
    variables.update(FACTS)
    variables.update(overrides)

    env = Environment(
        loader=FileSystemLoader(os.path.join(ROOT, "roles", role, "templates")),
        trim_blocks=True,
        undefined=StrictUndefined,
    )
    env.filters["to_json"] = lambda value, **kwargs: json.dumps(value)
    env.filters["to_nice_json"] = lambda value, **kwargs: json.dumps(value, indent=2)
    env.filters["bool"] = lambda value: (
        value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1", "on")
    )

    for name in SERVER_LISTS:
        if isinstance(variables.get(name), str):
            variables[name] = yaml.safe_load(env.from_string(variables[name]).render(**variables))

    # Ansible resolves variable level templates lazily; do it here up front.
    for _attempt in range(12):
        changed = False
        for key, value in list(variables.items()):
            if not isinstance(value, str) or ("{{" not in value and "{%" not in value):
                continue
            rendered = env.from_string(value).render(**variables).strip()
            try:
                rendered = yaml.safe_load(rendered)
            except yaml.YAMLError:
                pass
            if rendered != value:
                variables[key] = rendered
                changed = True
        if not changed:
            break

    return env, variables


def check_hcl(text):
    # Nomad and Consul parse their config with HCL1, which allows quoted
    # attribute keys and documents them for options, meta and node_meta.
    # python-hcl2 is HCL2 only, so normalise those keys before parsing.
    text = re.sub(
        r'^(\s*)"([^"]+)" =',
        lambda m: "%s%s =" % (m.group(1), re.sub(r"[^A-Za-z0-9_]", "_", m.group(2))),
        text,
        flags=re.M,
    )
    hcl2.load(io.StringIO(text))


def main():
    if hcl2 is None:
        print("note: python-hcl2 not installed, only rendering", file=sys.stderr)

    failures = 0
    for role, template, overrides in CASES + RENDER_ONLY:
        label = "%s/%s %s" % (role, template, json.dumps(sorted(overrides)))
        try:
            env, variables = role_environment(role, overrides)
            rendered = env.get_template(template).render(**variables)
        except Exception as error:  # noqa: BLE001 - report and keep going
            failures += 1
            print("RENDER FAIL %s\n  %s" % (label, error))
            continue

        if hcl2 is not None and (role, template, overrides) in CASES:
            try:
                check_hcl(rendered)
            except Exception as error:  # noqa: BLE001
                failures += 1
                print("HCL FAIL %s\n  %s" % (label, error))
                continue

        print("ok %s" % label)

    if failures:
        print("\n%d failure(s)" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
