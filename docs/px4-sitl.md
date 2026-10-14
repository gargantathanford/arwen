# Passive PX4 SITL telemetry

## Status and versions

Target: PX4 **v1.16.0**, Gazebo Harmonic, Ubuntu 22.04/24.04, real-time
simulation (`PX4_SIM_SPEED_FACTOR=1`). Development validation used Python
3.12.14, pymavlink 2.4.50 on Debian 13. PX4/Gazebo and QGroundControl are not
installed on this cloud machine. **Live SITL, QGroundControl concurrency and Mac
VM networking were not run.** The firmware commands below were checked against
PX4's official documentation and v1.16.0 startup, setup and message-stream source;
they have not been executed here. Fake and real-byte tests do not prove live
integration or flight readiness.

Validation completed: 53 tests passed with the MAVLink extra; the same installed
wheel outside the checkout passed 51 tests with 2 optional wire tests skipped
without pymavlink. Ruff lint/format, strict source mypy, dependency checks and
sdist/wheel builds passed. A terminal-monitor run against an encoded local UDP
fixture verified position/freshness output, heartbeat loss, exit status and
socket cleanup. This fixture was not PX4 SITL. Tool versions: pytest 9.1.1,
Ruff 0.17.0, mypy 2.4.0 and build 1.6.1.

## Install outside the ARWEN checkout

On an Ubuntu 22.04/24.04 simulation machine:

```bash
mkdir -p "$HOME/simulators"
cd "$HOME/simulators"
git clone --branch v1.16.0 --recursive https://github.com/PX4/PX4-Autopilot.git
cd PX4-Autopilot
bash Tools/setup/ubuntu.sh --no-nuttx
# Reboot if the setup script requests it, then return to this directory.
HEADLESS=1 PX4_SIM_SPEED_FACTOR=1 make px4_sitl gz_x500
```

Use the documented host setup; do not run Ubuntu apt setup on Debian or macOS.
The first build downloads models and builds firmware. No ARWEN installation
belongs inside the PX4 checkout. The setup script installs Gazebo Harmonic on
supported Ubuntu versions. For a graphical simulator omit `HEADLESS=1`.

In another terminal, from ARWEN's checkout:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,mavlink]'
python -m arwen.applications.telemetry --bind-host 127.0.0.1 --bind-port 14540 \
  --system-id 1 --component-id 1 --poll 0.1 --display-interval 0.5 \
  --connection-timeout 10 --heartbeat-timeout 3 --max-state-age 1
```

`.[mavlink]` is sufficient without development tools. `--help` lists monitor
settings. Invalid endpoint/timing configuration fails before opening a socket.
Missing pymavlink reports the extra to install. Ctrl-C closes the socket and
returns success; startup or heartbeat loss reports a reason and returns code 1.
The Python API supports explicit `open()` after loss; the terminal application
exits so restart its command to reconnect. No automatic retry loop exists.

## UDP and concurrent QGroundControl

An ARWEN bind address is a **local listening interface**, not PX4's destination.
`127.0.0.1:14540` accepts same-machine traffic. `0.0.0.0:14540` listens on every
IPv4 interface. PX4's default single-instance API link sends to remote **14540**
from local **14580**. ARWEN sends only TIMESYNC requests back to the selected
heartbeat's UDP peer. QGroundControl normally listens on **14550** and uses a
separate PX4 GCS link (local **18570**). Run QGroundControl independently using
its [official installation guide](https://docs.qgroundcontrol.com/master/en/qgc-user-guide/getting_started/download_and_install.html).
Do not bind QGroundControl and ARWEN to the same port or share an API socket with
another application. No QGroundControl installation/version was validated here.

If default telemetry is absent, inspect `mavlink status` in the **PX4 simulator
shell**. To explicitly enable the position stream on the default API link:

```text
mavlink stream -u 14580 -s LOCAL_POSITION_NED -r 10
```

This is simulator-side stream configuration, not an ARWEN transmission.
No parameter writes, vehicle command messages or flight operations are needed.
Source IDs must match the expected aircraft; changing the UDP port does not
change system identity. Additional PX4 instances use API ports 14541, 14542, etc.

## Mac client with Linux VM simulator

Install Python 3.12 and ARWEN with the same virtual-environment commands on the
Mac. Prefer bridged or host-only VM networking with reachable Mac and VM IPv4
addresses. Bind ARWEN to the Mac's receiving address (or `0.0.0.0`), **not** the
VM's address. Permit incoming UDP 14540 in the Mac firewall and return UDP to the
VM's PX4 sender port. NAT often requires explicit port forwarding and a host
reachable destination; a VM's loopback address never refers to the Mac.

To preserve local API/GCS links, add separate simulator-side links. Replace
`192.168.1.20` with the reachable Mac IPv4 address, in the PX4 shell:

```text
mavlink start -u 14581 -o 14540 -t 192.168.1.20 -m onboard -r 100000
mavlink stream -u 14581 -s LOCAL_POSITION_NED -r 10
mavlink start -u 18571 -o 14550 -t 192.168.1.20 -m normal -r 100000
```

Use free local ports if these are occupied. The first link is ARWEN telemetry;
the second is optional QGroundControl on the Mac. PX4 sends to the specified Mac
remote ports; ARWEN replies to 14581 for TIMESYNC. Avoid broadcast assumptions,
check `mavlink status`, and verify routing/firewalls before changing application
IDs or timing thresholds. MAVLink IDs and UDP peer pinning do not authenticate
senders; use a trusted simulation network.

## Timing and frame interpretation

The monitor distinguishes heartbeat health, clock quality, position availability
and freshness. A healthy heartbeat alone cannot establish fresh position. It
shows `position=None` until a correlated TIMESYNC reply with acceptable RTT and a
valid position sample arrive. TIMESYNC-only housekeeping is sent at most once per
configured interval from system 255/component 191. There are no heartbeat,
command, stream-request, parameter or setpoint transmissions from ARWEN.

PX4 v1.16.0 replies with `hrt_absolute_time()` in nanoseconds; position
`time_boot_ms` is the local-position publication timestamp in milliseconds.
Alignment uses a conservative lower bound, a quantization allowance and a 100 ppm
relative drift assumption. It is not precise sensor acquisition timing or a
hard guarantee under arbitrary clock behavior. Alignment expires after 5 seconds
by default; RTT above 0.1 seconds is rejected. Accelerated simulation and remote
clocks outside the drift bound require a different policy and are unsupported.
No raw boot timestamp is exposed as a host timestamp.

`origin_id` labels the selected PX4 estimator session, not an application-defined
home. Reconnection and detected clock restart replace this identity and invalidate
position. Duplicate/reordered samples do not refresh state; 32-bit millisecond
wrap is handled. A reboot cannot be distinguished from reordered position alone;
TIMESYNC discontinuity or heartbeat loss is required. LOCAL_POSITION_NED carries
no estimator-reset counter or validity flags; unseen estimator origin resets and
finite but invalid estimator values cannot be identified from these messages.
No conversion to an unrelated origin or claim of navigational validity is made.

## Opt-in live smoke procedure (not run here)

1. Start the pinned SITL, QGroundControl if desired, and the monitor above.
   Expect `healthy`, `bounded`, finite NED position and eventually `fresh=True`.
   A stationary valid `(0,0,0)` position is preserved.
2. Save the firmware version, airframe, source IDs and displayed estimator origin.
3. Stop SITL with Ctrl-C. Position age becomes stale if displayed before timeout;
   by the heartbeat timeout the adapter reports loss, invalidates its cache,
   closes its socket and the terminal monitor exits with code 1.
4. Restart SITL and rerun the monitor explicitly. Confirm healthy telemetry,
   a new estimator-session origin and no restored position from the old session.
5. Stop the monitor with Ctrl-C. Confirm it exits cleanly and the UDP port can
   be rebound. Confirm QGroundControl remains independent.

These observations must be recorded before claiming live PX4 integration works.
The next stage is safety and authority before any active control.

## Primary references consulted

- [MAVLink common messages](https://mavlink.io/en/messages/common.html)
- [Heartbeat](https://mavlink.io/en/services/heartbeat.html)
- [TIMESYNC](https://mavlink.io/en/services/timesync.html)
- [PX4 simulation](https://docs.px4.io/main/en/simulation/)
- [PX4 v1.16.0 API/GCS startup](https://github.com/PX4/PX4-Autopilot/blob/v1.16.0/ROMFS/px4fmu_common/init.d-posix/px4-rc.mavlink)
- [PX4 v1.16.0 TIMESYNC](https://github.com/PX4/PX4-Autopilot/blob/v1.16.0/src/modules/mavlink/mavlink_timesync.cpp)
- [PX4 v1.16.0 local position stream](https://github.com/PX4/PX4-Autopilot/blob/v1.16.0/src/modules/mavlink/streams/LOCAL_POSITION_NED.hpp)

The documentation web hosts were proxy-blocked during development. Their official
GitHub sources (`mavlink/mavlink`, `mavlink/mavlink-devguide`, `PX4/PX4-user_guide`)
and the pinned firmware sources above were read instead.
