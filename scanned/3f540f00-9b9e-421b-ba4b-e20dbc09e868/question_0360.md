# Q360: get_bridge_records_in_range bridge-node crash from public input

## Question
Can an ordinary bridge user reach `get_bridge_records_in_range` with crafted source_chain_id, start_seq_num, end_seq_num and trigger a panic, invariant violation, or resource-exhaustion path in unmodified bridge-node software?

## Target
- File/function: crates/sui-bridge/src/sui_client.rs::get_bridge_records_in_range
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: source_chain_id, start_seq_num, end_seq_num
- Exploit idea: Stress malformed but user-deliverable bridge payloads, event streams, or parsing boundaries until a fatal path appears.
- Invariant to test: Public bridge-facing input must fail closed without panicking, exhausting memory, or killing the node.
- Expected Immunefi impact: Low or Medium — remote crash or shutdown of bridge-related unmodified node software.
- Fast validation: Send malformed or oversized locally generated bridge inputs and observe whether the node exits, panics, or wedges.
