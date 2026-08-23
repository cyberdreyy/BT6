# Q4281: receive_and_buffer::send_transactions — scheduler starvation

## Question
Can an unprivileged attacker, through QUIC packets/transactions sent to the TPU by an unstaked client, reach `receive_and_buffer::send_transactions` and submit conflicting or low-fee transactions that make the scheduler stall or starve higher-fee ones, so that the invariant "the scheduler makes progress and honors priority ordering" is violated, leading to DoS (non-RPC)?

## Target
- File/function: `core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs` -> `send_transactions`
- Entrypoint: QUIC packets/transactions sent to the TPU by an unstaked client
- Attacker controls: account conflict structure and fee of transactions it submits
- Exploit idea: Submit conflicting or low-fee transactions that make the scheduler stall or starve higher-fee ones.
- Invariant to test: the scheduler makes progress and honors priority ordering.
- Expected Immunefi impact: DoS (non-RPC) — High
- Fast validation: write a streamer/nonblocking test driving the crafted QUIC/packet pattern and asserting the rate/memory bound holds.
