# Q4008: qos::try_add_connection — packet-parse panic

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `qos::try_add_connection` and send a malformed packet whose parse path in perf/streamer panics or indexes out of bounds, so that the invariant "malformed packets are rejected without panicking a worker thread" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/nonblocking/qos.rs` -> `try_add_connection`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the raw bytes of a packet it sends to the TPU port
- Exploit idea: Send a malformed packet whose parse path in perf/streamer panics or indexes out of bounds.
- Invariant to test: malformed packets are rejected without panicking a worker thread.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
