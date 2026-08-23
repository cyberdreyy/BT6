# Q4198: packet::to_packet_batches — forwarding-container abuse

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `packet::to_packet_batches` and fill the forwarding_stage packet_container so forwarding drops or mis-accounts legitimate packets, so that the invariant "the forwarding container is bounded and accounts packets correctly" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `perf/src/packet.rs` -> `to_packet_batches`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the volume of forwardable transactions it submits
- Exploit idea: Fill the forwarding_stage packet_container so forwarding drops or mis-accounts legitimate packets.
- Invariant to test: the forwarding container is bounded and accounts packets correctly.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
