# Q4206: sigverify_stage::join — banking-container overflow

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `sigverify_stage::join` and flood transactions so the transaction_state_container or receive_and_buffer bounds are exceeded or mis-evicted, so that the invariant "the banking buffer is bounded and evicts lowest-priority first" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `core/src/sigverify_stage.rs` -> `join`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the volume and priority of transactions it submits
- Exploit idea: Flood transactions so the transaction_state_container or receive_and_buffer bounds are exceeded or mis-evicted.
- Invariant to test: the banking buffer is bounded and evicts lowest-priority first.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
