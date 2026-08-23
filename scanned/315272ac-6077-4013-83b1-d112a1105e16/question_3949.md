# Q3949: quic::remove_connection — forwarding-container abuse

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `quic::remove_connection` and fill the forwarding_stage packet_container so forwarding drops or mis-accounts legitimate packets, so that the invariant "the forwarding container is bounded and accounts packets correctly" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/nonblocking/quic.rs` -> `remove_connection`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the volume of forwardable transactions it submits
- Exploit idea: Fill the forwarding_stage packet_container so forwarding drops or mis-accounts legitimate packets.
- Invariant to test: the forwarding container is bounded and accounts packets correctly.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
