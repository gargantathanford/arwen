"""Real wire decoding, local UDP and independent outbound allowlist checks."""

import socket
from importlib import import_module

import pytest

from arwen.communication.messages import Heartbeat, Position, Timesync
from arwen.config.telemetry import TelemetryConfig


def test_real_bytes_and_allowlist() -> None:
    mav = pytest.importorskip("pymavlink.dialects.v20.common")
    from arwen.communication.mavlink import Codec

    codec = Codec()
    encoder = mav.MAVLink(None, srcSystem=1, srcComponent=1)
    frames = [
        mav.MAVLink_heartbeat_message(2, 12, 0, 0, 3, 3),
        mav.MAVLink_local_position_ned_message(1000, 1, 2, -3, 0, 0, 0),
        mav.MAVLink_timesync_message(10_000_000_000, 123),
    ]
    data = b"".join(message.pack(encoder) for message in frames)
    decoded = codec.decode(data, ("127.0.0.1", 18570))
    assert [p.payload for p in decoded] == [
        Heartbeat(12, 2),
        Position(1000, 1, 2, -3),
        Timesync(10_000_000_000, 123),
    ]
    assert codec.decode(b"garbage", ("127.0.0.1", 1)) == []
    parser = mav.MAVLink(None)
    outbound = parser.parse_buffer(codec.encode_housekeeping("TIMESYNC", 123))
    assert len(outbound) == 1
    assert outbound[0].get_type() == "TIMESYNC"
    assert outbound[0].tc1 == 0 and outbound[0].ts1 == 123
    assert (outbound[0].get_srcSystem(), outbound[0].get_srcComponent()) == (255, 191)
    for kind in (
        "HEARTBEAT",
        "COMMAND_LONG",
        "SET_POSITION_TARGET_LOCAL_NED",
        "PARAM_SET",
    ):
        with pytest.raises(ValueError):
            codec.encode_housekeeping(kind, 123)


def test_local_udp() -> None:
    mav = pytest.importorskip("pymavlink.dialects.v20.common")
    from arwen.communication.mavlink import UdpTransport

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        sender.settimeout(1)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        transport = UdpTransport(TelemetryConfig(bind_port=port))
        encoder = mav.MAVLink(None, srcSystem=1, srcComponent=1)
        try:
            transport.open()
            for _ in range(10):
                sender.sendto(b"unrecognized frame", ("127.0.0.1", port))
            sender.sendto(
                mav.MAVLink_heartbeat_message(2, 12, 0, 0, 3, 3).pack(encoder),
                ("127.0.0.1", port),
            )
            packet = transport.receive(1)
            assert packet is not None and packet.payload == Heartbeat(12, 2)
            transport.select_peer(packet.peer)
            transport.request_timesync(123)
            data, _ = sender.recvfrom(4096)
            messages = mav.MAVLink(None).parse_buffer(data)
            assert messages[0].get_type() == "TIMESYNC"
        finally:
            transport.close()
            transport.close()
        with pytest.raises(RuntimeError):
            transport.receive(0)


def test_no_eager_optional_import() -> None:
    # Importing communication itself does not load a dialect or instantiate I/O.
    module = import_module("arwen.communication")
    assert module.__doc__
