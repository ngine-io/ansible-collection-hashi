"""Render the collection's role templates into directories a real agent can load.

Reuses tests/render_templates.py so the driver renders exactly what the roles
render, rather than a second approximation of it.

Usage: render_live.py <collection-root> <out-dir> <profile> [key=value ...]

Profiles:
  nomad-server   nomad.hcl + server.hcl
  nomad-client   nomad.hcl + client.hcl
  nomad-both     nomad.hcl + server.hcl + client.hcl
  consul-server  consul.hcl
  consul-client  consul.hcl
"""

import importlib.util
import json
import os
import sys

PROFILES = {
    "nomad-server": ("nomad", ["nomad.hcl.j2", "server.hcl.j2"], {"nomad__roles": ["server"]}),
    "nomad-client": ("nomad", ["nomad.hcl.j2", "client.hcl.j2"], {"nomad__roles": ["client"]}),
    "nomad-both": (
        "nomad",
        ["nomad.hcl.j2", "server.hcl.j2", "client.hcl.j2"],
        {"nomad__roles": ["server", "client"]},
    ),
    "consul-server": ("consul", ["consul.hcl.j2"], {"consul__role": "server"}),
    "consul-client": ("consul", ["consul.hcl.j2"], {"consul__role": "client"}),
}


def load_render_harness(root):
    path = os.path.join(root, "tests", "render_templates.py")
    spec = importlib.util.spec_from_file_location("render_templates", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_templates"] = module
    spec.loader.exec_module(module)
    return module


def main():
    root, out, profile = sys.argv[1], sys.argv[2], sys.argv[3]
    if profile not in PROFILES:
        sys.exit("unknown profile %r, expected one of %s" % (profile, ", ".join(sorted(PROFILES))))

    role, templates, overrides = PROFILES[profile]
    overrides = dict(overrides)
    for pair in sys.argv[4:]:
        key, _sep, raw = pair.partition("=")
        try:
            overrides[key] = json.loads(raw)
        except ValueError:
            overrides[key] = raw

    harness = load_render_harness(root)
    env, variables = harness.role_environment(role, overrides)

    os.makedirs(out, exist_ok=True)
    for template in templates:
        rendered = env.get_template(template).render(**variables)
        with open(os.path.join(out, template[: -len(".j2")]), "w") as handle:
            handle.write(rendered)
        print("wrote %s/%s" % (out, template[: -len(".j2")]))


if __name__ == "__main__":
    main()
