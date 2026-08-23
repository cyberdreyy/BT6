# Q3996: stream_throttle::max_streams_per_ms — connection-rate-limit bypass

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `stream_throttle::max_streams_per_ms` and open QUIC connections in a pattern that defeats connection_rate_limiter or ConnectionTable eviction, starving legitimate connections, so that the invariant "per-source connection admission is bounded and fair on default config" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/nonblocking/stream_throttle.rs` -> `max_streams_per_ms`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: QUIC connection open rate and source keying from one unstaked client
- Exploit idea: Open QUIC connections in a pattern that defeats connection_rate_limiter or ConnectionTable eviction, starving legitimate connections.
- Invariant to test: per-source connection admission is bounded and fair on default config.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
