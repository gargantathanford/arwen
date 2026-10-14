# ADR 0002: First local-position contracts

Status: accepted for Stage 1.

Use a single local-position request as the first contract consumer. This is a
bounded development example, not a selected aircraft mission or control loop.
Contracts live in `vehicle`; explicit timing configuration lives in `config`.
No new internal import edges or runtime dependencies are introduced.

Positions are finite SI metres in NED coordinates with an explicit origin ID.
Unknown position is distinct from zero. Measurement and receipt timestamps share
an injected monotonic clock domain; a future adapter must normalize external
timestamps into that domain. Measurement age determines freshness, and the age
limit is exclusive. Future or reordered timestamps cannot establish freshness.

Requests carry correlation IDs, authority grant IDs and exclusive expiration
deadlines. An authority ID is a reference, not authentication. A synchronous
adapter protocol returns submission outcomes; acceptance never asserts vehicle
movement, controller acknowledgement or physical completion. Those later events
need separate contracts before active integration.

A deterministic test fake enforces authority, freshness, capabilities, frame
agreement, expiration and duplicate rejection without I/O. Revoking fake
authority prevents subsequent requests; there are no queued or repeated outputs.
The fake is test support, not a production safety implementation. Transport
lifecycle, real authority grants, cancellation, acknowledgement tracking and
mission intent remain later work. No firmware, simulator or aircraft is accessed.
