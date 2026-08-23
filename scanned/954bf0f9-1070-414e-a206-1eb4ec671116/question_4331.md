# Q4331: scheduler_controller::enters_on_capacity_drops_even_below_watermark — banking-container overflow

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `scheduler_controller::enters_on_capacity_drops_even_below_watermark` and flood transactions so the transaction_state_container or receive_and_buffer bounds are exceeded or mis-evicted, so that the invariant "the banking buffer is bounded and evicts lowest-priority first" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `core/src/banking_stage/transaction_scheduler/scheduler_controller.rs` -> `enters_on_capacity_drops_even_below_watermark`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: the volume and priority of transactions it submits
- Exploit idea: Flood transactions so the transaction_state_container or receive_and_buffer bounds are exceeded or mis-evicted.
- Invariant to test: the banking buffer is bounded and evicts lowest-priority first.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
