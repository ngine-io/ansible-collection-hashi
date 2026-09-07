#!/usr/bin/env bash
# Build, launch and drive the ngine_io.hashi collection.
#
# There is no "app" to open here: the collection's job is to render Nomad and
# Consul configuration and to drive their APIs. So this driver stands up real
# nomad and consul agents from the collection's own templates, then runs the
# collection's modules and roles against them.
#
# Usage: .claude/skills/run-hashi/driver.sh <command>
#   setup     create the venv, fetch binaries, build the collection tree
#   check     ansible-test sanity + units, ansible-lint, template render
#   validate  render every role profile and validate it with the real binaries
#   up        start nomad (server+client) and consul from rendered config
#   drive     run the modules and roles against the live agents
#   down      stop the agents
#   all       setup, check, validate, up, drive, down
#   status    what is running
#
# Everything lands in $HASHI_RUN_DIR (default /tmp/hashi-run); the repo is
# never written to.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUN_DIR="${HASHI_RUN_DIR:-/tmp/hashi-run}"
VENV="$RUN_DIR/venv"
BIN="$RUN_DIR/bin"
# ansible-test refuses a collection root that is a symlink or that has no
# ansible_collections/<ns>/<name> parent, so the tree is a real copy.
TREE="$RUN_DIR/ansible_collections/ngine_io/hashi"
PY="$VENV/bin/python"
SKILL="$ROOT/.claude/skills/run-hashi"

NOMAD_HTTP=4646
CONSUL_HTTP=8500

log() { printf '\n=== %s ===\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

nomad_bin() { have nomad && echo nomad || echo "$BIN/nomad"; }
consul_bin() { have consul && echo consul || echo "$BIN/consul"; }

sync_tree() {
  mkdir -p "$TREE"
  rsync -a --delete --exclude '.git' --exclude 'tests/output' "$ROOT/" "$TREE/"
}

cmd_setup() {
  mkdir -p "$RUN_DIR" "$BIN"

  if [ ! -x "$PY" ]; then
    log "creating venv at $VENV"
    python3 -m venv "$VENV"
  fi
  log "installing python dependencies"
  # py-consul is pinned: community.general.consul uses the pre-1.3 API shape
  # (consul.Consul.Agent), and 1.5+ fails with
  # "type object 'Consul' has no attribute 'Agent'".
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q \
    'ansible-core' ansible-lint \
    jinja2 pyyaml python-hcl2 \
    pytest pytest-xdist \
    'py-consul==1.2.4'

  log "installing collection dependencies"
  ANSIBLE_COLLECTIONS_PATH="$RUN_DIR" "$VENV/bin/ansible-galaxy" collection install --force \
    -r "$ROOT/tests/requirements.yml" -p "$RUN_DIR" >/dev/null

  if ! have nomad; then
    log "fetching nomad"
    local v
    v=$(curl -sS https://releases.hashicorp.com/nomad/index.json |
      "$PY" -c 'import json,sys;print(sorted((v for v in json.load(sys.stdin)["versions"] if v.replace(".","").isdigit()),key=lambda s:[int(p) for p in s.split(".")])[-1])')
    curl -sS -o "$RUN_DIR/nomad.zip" "https://releases.hashicorp.com/nomad/${v}/nomad_${v}_linux_amd64.zip"
    unzip -oq "$RUN_DIR/nomad.zip" -d "$BIN"
  fi
  if ! have consul; then
    log "fetching consul"
    local v
    v=$(curl -sS https://releases.hashicorp.com/consul/index.json |
      "$PY" -c 'import json,sys;print(sorted((v for v in json.load(sys.stdin)["versions"] if v.replace(".","").isdigit()),key=lambda s:[int(p) for p in s.split(".")])[-1])')
    curl -sS -o "$RUN_DIR/consul.zip" "https://releases.hashicorp.com/consul/${v}/consul_${v}_linux_amd64.zip"
    unzip -oq "$RUN_DIR/consul.zip" -d "$BIN"
  fi

  sync_tree
  log "setup complete"
  "$(nomad_bin)" version | head -1
  "$(consul_bin)" version | head -1
}

cmd_check() {
  sync_tree
  log "ansible-test sanity"
  (cd "$TREE" && ANSIBLE_COLLECTIONS_PATH="$RUN_DIR" "$VENV/bin/ansible-test" sanity --local --color no)
  log "ansible-test units"
  (cd "$TREE" && ANSIBLE_COLLECTIONS_PATH="$RUN_DIR" "$VENV/bin/ansible-test" units --local --color no)
  log "ansible-lint"
  (cd "$TREE" && ANSIBLE_COLLECTIONS_PATH="$RUN_DIR" "$VENV/bin/ansible-lint" --offline)
  log "syntax check of the documented playbooks"
  (cd "$TREE" && ANSIBLE_COLLECTIONS_PATH="$RUN_DIR" "$VENV/bin/ansible-playbook" \
    -i tests/inventory --syntax-check tests/playbooks/*.yml)
  log "template render + HCL parse"
  (cd "$TREE" && "$PY" tests/render_templates.py)
}

# Render one profile and hand it to the real binary. This catches what the
# python HCL parser in tests/ cannot: options a given agent version rejects.
cmd_validate() {
  local cfg="$RUN_DIR/validate"
  rm -rf "$cfg"
  local gossip consul_key
  gossip=$("$(nomad_bin)" operator gossip keyring generate)
  consul_key=$("$(consul_bin)" keygen)

  for profile in nomad-server nomad-client nomad-both; do
    mkdir -p "$cfg/$profile/data"
    "$PY" "$SKILL/render_live.py" "$ROOT" "$cfg/$profile" "$profile" \
      "nomad__data_path=$cfg/$profile/data" \
      "nomad__server_encrypt=$gossip" >/dev/null
    log "nomad config validate: $profile"
    # Validate the directory, not a single file: the role splits the config
    # across nomad.hcl, server.hcl and client.hcl and Nomad merges them.
    "$(nomad_bin)" config validate "$cfg/$profile" 2>&1 | grep -v '^WARNING: mTLS'
  done

  for profile in consul-server consul-client; do
    mkdir -p "$cfg/$profile/data"
    "$PY" "$SKILL/render_live.py" "$ROOT" "$cfg/$profile" "$profile" \
      "consul__data_dir=$cfg/$profile/data" \
      "consul__encrypt=$consul_key" >/dev/null
    log "consul validate: $profile"
    "$(consul_bin)" validate "$cfg/$profile"
  done
}

wait_for() {
  local url="$1" what="$2" tries="${3:-40}"
  for _ in $(seq "$tries"); do
    if curl -sSf --max-time 2 "$url" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "timed out waiting for $what at $url" >&2
  return 1
}

cmd_up() {
  cmd_down >/dev/null 2>&1 || true
  local live="$RUN_DIR/live"
  rm -rf "$live"
  mkdir -p "$live/nomad" "$live/consul" "$live/nomad-data" "$live/consul-data"

  local gossip consul_key
  gossip=$("$(nomad_bin)" operator gossip keyring generate)
  consul_key=$("$(consul_bin)" keygen)

  log "rendering a single node cluster from the role templates"
  # bootstrap_expect comes from the inventory group size, which is 3 in the
  # test fixtures; a one node cluster has to override it or it never elects.
  "$PY" "$SKILL/render_live.py" "$ROOT" "$live/nomad" nomad-both \
    "nomad__data_path=$live/nomad-data" \
    "nomad__server_encrypt=$gossip" \
    'nomad__bind_address=127.0.0.1' \
    'nomad__advertise_address=127.0.0.1' \
    'nomad__server_bootstrap_expect=1' \
    'nomad__server_retry_join=[]' \
    'nomad__servers=["127.0.0.1"]' \
    'nomad__use_consul=true'

  "$PY" "$SKILL/render_live.py" "$ROOT" "$live/consul" consul-server \
    "consul__data_dir=$live/consul-data" \
    "consul__encrypt=$consul_key" \
    'consul__bind_address=127.0.0.1' \
    'consul__advertise_address=127.0.0.1' \
    'consul__bootstrap_expect=1' \
    'consul__retry_join=[]' \
    'consul__servers=["127.0.0.1"]' \
    'consul__client_addresses=["127.0.0.1"]'

  log "starting consul"
  nohup "$(consul_bin)" agent -config-dir "$live/consul" >"$live/consul.log" 2>&1 &
  echo $! >"$live/consul.pid"
  wait_for "http://127.0.0.1:$CONSUL_HTTP/v1/status/leader" consul

  log "starting nomad"
  nohup "$(nomad_bin)" agent -config "$live/nomad" >"$live/nomad.log" 2>&1 &
  echo $! >"$live/nomad.pid"
  wait_for "http://127.0.0.1:$NOMAD_HTTP/v1/status/leader" nomad

  # The client registers a little after the server answers.
  for _ in $(seq 30); do
    if curl -sS --max-time 2 "http://127.0.0.1:$NOMAD_HTTP/v1/nodes" | grep -q '"Status":"ready"'; then break; fi
    sleep 1
  done
  cmd_status
}

cmd_drive() {
  sync_tree
  local live="$RUN_DIR/live"
  local consul_enabled=false
  curl -sSf --max-time 2 "http://127.0.0.1:$CONSUL_HTTP/v1/status/leader" >/dev/null 2>&1 && consul_enabled=true

  log "driving the modules and roles against the live agents"
  # ansible_python_interpreter has to point at the venv: with -c local Ansible
  # discovers the system python, which does not have py-consul.
  ANSIBLE_COLLECTIONS_PATH="$RUN_DIR" \
  ANSIBLE_HOST_KEY_CHECKING=False \
    "$VENV/bin/ansible-playbook" -i localhost, -c local \
    -e "ansible_python_interpreter=$PY" \
    -e "nomad_addr=http://127.0.0.1:$NOMAD_HTTP" \
    -e "consul_addr=http://127.0.0.1:$CONSUL_HTTP" \
    -e "consul_enabled=$consul_enabled" \
    --diff "$SKILL/drive.yml"

  log "catalog after the run"
  curl -sS --max-time 5 "http://127.0.0.1:$CONSUL_HTTP/v1/catalog/services" 2>/dev/null || true
  echo
}

# Kill any nomad/consul agent whose command line points into our run dir.
# The pid files are the fast path; this is the one that actually guarantees
# nothing is left holding 4646 or 8500.
kill_strays() {
  local signal="$1" found=1
  for name in nomad consul; do
    for pid in $(pgrep -x "$name" 2>/dev/null); do
      if tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q -- "$RUN_DIR"; then
        kill "-$signal" "$pid" 2>/dev/null || true
        found=0
      fi
    done
  done
  return $found
}

ports_free() {
  ! curl -sSf --max-time 1 "http://127.0.0.1:$NOMAD_HTTP/v1/status/leader" >/dev/null 2>&1 &&
    ! curl -sSf --max-time 1 "http://127.0.0.1:$CONSUL_HTTP/v1/status/leader" >/dev/null 2>&1
}

cmd_down() {
  local live="$RUN_DIR/live"
  for name in nomad consul; do
    if [ -f "$live/$name.pid" ]; then
      kill "$(cat "$live/$name.pid")" 2>/dev/null || true
      rm -f "$live/$name.pid"
    fi
  done

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    ports_free && { log "stopped"; return 0; }
    kill_strays TERM || true
    sleep 1
  done

  kill_strays KILL || true
  sleep 2
  if ports_free; then
    log "stopped (needed SIGKILL)"
  else
    echo "WARNING: something is still listening on $NOMAD_HTTP or $CONSUL_HTTP" >&2
  fi
}

cmd_status() {
  log "status"
  for pair in "nomad $NOMAD_HTTP" "consul $CONSUL_HTTP"; do
    set -- $pair
    if curl -sSf --max-time 2 "http://127.0.0.1:$2/v1/status/leader" >/dev/null 2>&1; then
      printf '%-7s up   leader=%s\n' "$1" "$(curl -sS "http://127.0.0.1:$2/v1/status/leader")"
    else
      printf '%-7s down\n' "$1"
    fi
  done
  curl -sS --max-time 2 "http://127.0.0.1:$NOMAD_HTTP/v1/nodes" 2>/dev/null |
    "$PY" -c 'import json,sys
try:
    for n in json.load(sys.stdin):
        print("node    %s status=%s eligibility=%s" % (n["Name"], n["Status"], n["SchedulingEligibility"]))
except Exception:
    pass' || true
}

cmd_all() {
  cmd_setup
  cmd_check
  cmd_validate
  cmd_up
  cmd_drive
  cmd_down
}

case "${1:-all}" in
  setup) cmd_setup ;;
  check) cmd_check ;;
  validate) cmd_validate ;;
  up) cmd_up ;;
  drive) cmd_drive ;;
  down) cmd_down ;;
  status) cmd_status ;;
  all) cmd_all ;;
  *) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 1 ;;
esac
