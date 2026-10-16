# PX4 SITL end-to-end runbook

Supported flight target: **PX4 v1.16.0**, commit
`6ea3539157ca358c70a515878b77077af7d4611d`, **built-in SIH quadrotor X**, airframe
10040, default unaccelerated simulation. This is a local simulation profile only.
The normal telemetry monitor remains read-only; no aircraft acceptance is claimed.

## Install and build

Keep PX4 outside the ARWEN checkout. The build was exercised in Ubuntu 22.04
(Docker); its resulting native binary was run on this Debian Linux host. On an
Ubuntu 22.04 build host:

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build git python3-pip python3-dev \
  ca-certificates libxml2-utils zip unzip bc
mkdir -p /tmp/arwen-simulators
cd /tmp/arwen-simulators
git clone --depth 1 --branch v1.16.0 --recursive --shallow-submodules \
  https://github.com/PX4/PX4-Autopilot.git
cd PX4-Autopilot
# The pinned version-header generator expects a NuttX version tag even for SITL.
git -C platforms/nuttx/NuttX/nuttx fetch --depth 1 origin tag nuttx-10.3.0
python3 -m pip install --user -r Tools/setup/requirements.txt
make px4_sitl_default -j4
```

For an isolated Ubuntu container use the same packages/commands and a source bind
mount; run the built binary only where its Linux libraries are compatible. Build
outputs must be readable by the runtime user. Behind a TLS proxy, install its
trusted CA using the platform's supported trust configuration; never disable TLS,
Git verification or expected checksums. No Gazebo, ROS, PX4 source patch or GCS
installation is required for SIH. Ubuntu host commands require normal local admin
access; do not run Ubuntu package setup on macOS or Debian as if it were Ubuntu.

From ARWEN, with Python 3.12 and uv 0.12.23 available:

```bash
uv sync --locked --extra dev --extra mavlink --group build
source .venv/bin/activate
```

## Preferred repeatable acceptance command

Stop any other PX4 instance 0 or API client first. The following **explicit operator
action authorizes simulated arming/flight**. The harness starts a fresh simulator
for each case, sets only the documented simulation parameters, records real UDP
traffic and stops its own processes when done:

```bash
python tools/run_sitl_acceptance.py \
  --px4-build /tmp/arwen-simulators/PX4-Autopilot/build/px4_sitl_default \
  --authorize-sitl-flight --case all
```

Use `--case nominal`, `takeover`, `link-loss` or `mode-rejection` for one case.
`--output artifacts/px4-sitl/my-run` selects a new evidence directory. Existing
files/directories are not overwritten. The harness never manufactures vehicle
positions, acknowledgements or landed state. Default unit tests never launch it.

## Start the simulator and scenario separately

In one terminal (replace paths with your actual checkout/build):

```bash
mkdir -p /tmp/arwen-sih-rootfs
unset PX4_SIM_SPEED_FACTOR
PX4_SYS_AUTOSTART=10040 PX4_SIM_MODEL=sihsim_quadx \
PX4_PARAM_EKF2_MAG_TYPE=6 PX4_PARAM_COM_RC_IN_MODE=4 \
PX4_PARAM_COM_OF_LOSS_T=0.5 PX4_PARAM_COM_OBL_RC_ACT=4 \
PX4_PARAM_COM_DISARM_LAND=1 \
/tmp/arwen-simulators/PX4-Autopilot/build/px4_sitl_default/bin/px4 \
  /tmp/arwen-simulators/PX4-Autopilot/build/px4_sitl_default/etc \
  -w /tmp/arwen-sih-rootfs -s /absolute/path/to/arwen/tools/px4_sitl_startup.sh
```

Do not set `PX4_SIM_SPEED_FACTOR=1`: this PX4 startup script scales and overwrites
Offboard-loss timeout when that variable is present. Unset means SIH's default
1x speed while preserving the explicitly configured 0.5 s loss timeout.

In another terminal:

```bash
# Validates real telemetry and read-back parameters; does not arm or request modes.
python -m arwen.applications.px4_sitl

# Explicit operator action for this selected simulation:
python -m arwen.applications.px4_sitl --authorize-sitl-flight \
  --height 2 --north 2 --east 0 --radius 5 --ceiling 5 \
  --xy-tolerance 0.3 --z-tolerance 0.2 --dwell 1
```

Expected nominal states: PREFLIGHT → PRIMING → SETTING_MODE → ARMING → TAKEOFF →
MOVING → LANDING → WAIT_LANDED_DISARMED → COMPLETE. Before authorization the
vehicle must be landed/disarmed, in a supported ground mode, with two seconds of
stable estimator-session telemetry. The operator flag issues a bounded local
lease; no connection or armed bit implicitly grants authority.

## Endpoints and required parameters

ARWEN binds local `127.0.0.1:14540`; PX4's API sender is `127.0.0.1:14580`.
`--bind-port`, `--system-id` and `--component-id` are configurable. This active
profile restricts bind addresses to loopback and expects one local API controller.
A vehicle lock also prevents competing local ARWEN flight processes, even on
alternate ports. This is not network authentication or protection against an
unrelated MAVLink sender. Do not run a second Offboard application.

The harness uses a transparent fault proxy on 14540 and ARWEN on 14541; PX4 remains
on 14580. QGroundControl may independently use its usual 14550 GCS destination
(PX4 source 18570). Do not bind it to the API port. Remote/Mac/VM **active flight**
is outside this validated profile; the passive monitor can still be configured
separately. QGroundControl and physical RC takeover were not manually validated.

| Parameter | Required | Purpose |
| --- | --- | --- |
| SYS_AUTOSTART | 10040 | SIH quadrotor target, checked along with firmware version/capabilities. |
| EKF2_MAG_TYPE | 6 | Initialize heading from magnetometer; avoid the in-flight heading realignment of continuous mag fusion in this small simulation. Not a general aircraft recommendation. |
| COM_RC_IN_MODE | 4 | Ignore absent simulated stick input. Takeover tests use an explicit Hold-mode command, not RC sticks. |
| COM_OF_LOSS_T | 0.5 | Delay after PX4 detects loss of Offboard proof-of-life before its configured action. The internal detection window is additional. |
| COM_OBL_RC_ACT | 4 | PX4 selects Land after Offboard loss. |
| COM_DISARM_LAND | 1 | PX4 automatically disarms after landing; ARWEN does not force disarm. |

ARWEN reads these parameters; it sends no parameter writes. The simulator launch
sets them explicitly. The startup helper requests HEARTBEAT/EXTENDED_SYS_STATE at
5 Hz, LOCAL_POSITION_NED/ODOMETRY at 20 Hz and ESTIMATOR_STATUS at 10 Hz. Missing,
stale or invalid telemetry prevents flight, rather than being treated as zero.

## Protocol and interruption behavior

Commands 176 (Offboard), 400 (normal arm) and 21 (land at current position) have
bounded, serialized real COMMAND_ACK transactions. Confirmation additionally uses
new HEARTBEAT mode/armed observations. No automatic command retry or forced arming
exists. Version request 512 is read-only. The negative case requests unsupported
custom mode 255: selected PX4 may ACK it ACCEPTED without changing mode. The
observed-mode deadline must still fail before arming; ACK alone is never success.

Position-based takeoff and movement use 20 Hz SET_POSITION_TARGET_LOCAL_NED in
LOCAL_NED with mask 3576 (position only, yaw ignored). They have **no COMMAND_ACK**.
Height is relative to the initial local estimator position; it is not MSL altitude.
Fresh, distinct measurements must remain within XY/Z tolerances for the dwell
interval. Streams use a boot-aligned timestamp; selected PX4 stamps reception
internally. A conservative causal TIMESYNC policy handles SIH's slower/paused boot
clock; accelerated simulation is unsupported. Estimator resets before authorization
replace the frame and restart preflight stability. Any ambiguous reset during
control revokes authority; resets are not ignored for a nominal pass.

Interruption procedures:

- **B / operator takeover:** during MOVING enter `commander mode auto:loiter` in the PX4
  shell. ARWEN observes the mode change, revokes its lease and stops setpoints and
  flight commands. It does not reacquire control. The harness performs this real
  console action, then explicitly issues operator `land` to clean up the simulation.
- **C / link loss:** the harness drops both directions of API UDP for three seconds
  during MOVING. ARWEN aborts on stale data. Its output does not resume after link
  restoration; a bounded read-only reconnect records PX4's actual Land/landing/
  disarm response. The proxy continues observing real PX4 wire output while cut.
- **D / unavailable mode:** run `--mode-rejection-test --authorize-sitl-flight`.
  No prestream or arming is attempted. Rejection or missing requested-mode
  observation causes a bounded failure even if COMMAND_ACK said ACCEPTED.
- **Ctrl-C:** revokes authority and stops local output. It does not prove a physical
  stop. If still in Offboard, PX4 must execute its configured loss response.

During observed AUTO.LAND, ARWEN stops the Offboard stream and lets PX4 land and
then auto-disarm. PX4 can restore its earlier Offboard selection after disarm;
that ground-only transition is accepted only in final confirmation when fresh
landed and disarmed evidence agrees. It cannot mask an armed mode change.
Completion requires fresh landed and disarmed observations plus
a dwell interval. On failure it sends no emergency command that could override an
operator; it logs up to 20 seconds of observation-only recovery, never rearming.

## Logs, troubleshooting and evidence

Application JSONL files default to `artifacts/px4-sitl/<uuid>.jsonl` (or `--log`).
The harness stores `events.jsonl`, `wire.jsonl`, `scenario.log`, PX4 stdout and ULog
under each case directory, plus hashes and `result.json`/`summary.json`. These
artifacts and runtime rootfs files are ignored by Git. Events include UTC and
monotonic time, selected identity/frame, mode, armed/landed state, request IDs,
phase, targets, measured positions, ACKs, revocations and failure reasons.

If preflight fails, read its readiness reason: wrong version/parameters, missing
streams, estimator readiness, stale clock alignment or a pre-existing armed/mode
condition are blockers. Do not raise limits, disable checks, edit expected hashes
or force arm to turn a failed run into a pass. If builds fail after a shallow clone,
check the pinned NuttX tag above; proxy certificate errors require proper CA trust.
If another instance owns IPC/API ports, shut down that simulator/controller before
starting a fresh case. An ACK timeout means delivery is uncertain, not rejection.

Live acceptance ran on **2026-10-16**, on Linux with the unmodified pinned PX4
build. All four cases passed using real MAVLink output and SIH telemetry. Negative
cases deliberately exit 1 after ABORTED; the harness exits 0 when that failure and
the required PX4 response are verified. Earlier failed development attempts remain
in ignored artifacts and are not counted as passes.

Evidence root: `artifacts/px4-sitl/acceptance-04/`. Each row names its case directory;
`result.json` records full SHA-256 hashes of `events.jsonl` and `wire.jsonl`.

| Case | Status | Observed evidence |
| --- | --- | --- |
| A: nominal flight | PASS | `nominal/`: real Offboard/arm; 2 m takeoff and 2 m northward target; both arrivals held for 1 s; AUTO.LAND followed by landed/disarmed dwell at 05:06:48 UTC. Events SHA-256 starts `7ca8efddc7d9`. |
| B: operator takeover | PASS | `takeover/`: armed Hold observed during MOVING; authority revoked at 05:07:09 UTC; no subsequent ARWEN commands/setpoints or reacquisition. Explicit operator land performed cleanup. Events hash starts `5c9015545af5`. |
| C: link interruption/recovery | PASS | `link-loss/`: 3 s bidirectional drop during MOVING; stale-position abort; real PX4 AUTO.LAND and landed/disarmed observed; read-only reconnect without resumption/rearm. Events hash starts `e2590891f04d`. |
| D: unavailable control mode | PASS | `mode-rejection/`: real ACK ACCEPTED for unsupported mode 255, but mode never observed; bounded observation deadline aborted without arming or streaming. Events hash starts `4e13312f51e3`. |

Separate deterministic validation: Python 3.12 and 3.13 MAVLink profiles each
passed 225 tests; core-only Python 3.12 passed 222 with three optional wire tests
skipped. Lint, formatting, strict source/tool typing, import boundaries, lockfile,
wheel/sdist build and clean-install distribution checks passed. These are unit,
wire-format and packaging results, not additional live flights. Hosted CI, macOS,
QGroundControl/RC takeover and aircraft operation were not demonstrated.

## Primary sources checked

- [PX4 v1.16.0 Commander](https://github.com/PX4/PX4-Autopilot/blob/v1.16.0/src/modules/commander/Commander.cpp): mode, arm and land handling.
- [PX4 receiver](https://github.com/PX4/PX4-Autopilot/blob/v1.16.0/src/modules/mavlink/mavlink_receiver.cpp): local setpoint frame/mask and receipt timestamps.
- [PX4 commander parameters](https://github.com/PX4/PX4-Autopilot/blob/v1.16.0/src/modules/commander/commander_params.c): loss action, RC mode and auto-disarm.
- [PX4 estimator magnetometer policy](https://github.com/PX4/PX4-Autopilot/blob/v1.16.0/src/modules/ekf2/params_magnetometer.yaml).
- [PX4 EKF2](https://github.com/PX4/PX4-Autopilot/blob/v1.16.0/src/modules/ekf2/EKF2.cpp): odometry reset counter semantics.
- [MAVLink common messages](https://mavlink.io/en/messages/common.html), [command protocol](https://mavlink.io/en/services/command.html), [TIMESYNC](https://mavlink.io/en/services/timesync.html).
- [PX4 SIH](https://docs.px4.io/main/en/sim_sih/), [Offboard](https://docs.px4.io/main/en/flight_modes/offboard.html).

Official documentation GitHub sources were used where documentation hosts were
proxy-blocked. The pinned implementation sources take precedence over assumptions
from current main-branch docs. This runbook does not certify aircraft safety.
