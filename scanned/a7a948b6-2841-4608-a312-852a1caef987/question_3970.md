# Q3970: quic::poc_staked_eviction_committed_before_failed_admit — banking-container overflow

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `quic::poc_staked_eviction_committed_before_failed_admit` and flood transactions so the transaction_state_container or receive_and_buffer bounds are exceeded or mis-evicted, so that the invariant "the banking buffer is bounded and evicts lowest-priority first" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `streamer/src/nonblocking/quic.rs` -> `poc_staked_eviction_committed_before_failed_admit`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the volume and priority of transactions it submits
- Exploit idea: Flood transactions so the transaction_state_container or receive_and_buffer bounds are exceeded or mis-evicted.
- Invariant to test: the banking buffer is bounded and evicts lowest-priority first.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
