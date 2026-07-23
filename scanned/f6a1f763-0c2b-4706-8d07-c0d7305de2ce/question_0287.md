# Q287: get_eth_event_cursors bridge-node crash from public input

## Question
Can an ordinary bridge user reach `get_eth_event_cursors` with crafted contract_addresses and trigger a panic, invariant violation, or resource-exhaustion path in unmodified bridge-node software?

## Target
- File/function: crates/sui-bridge/src/storage.rs::get_eth_event_cursors
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: contract_addresses
- Exploit idea: Stress malformed but user-deliverable bridge payloads, event streams, or parsing boundaries until a fatal path appears.
- Invariant to test: Public bridge-facing input must fail closed without panicking, exhausting memory, or killing the node.
- Expected Immunefi impact: Low or Medium — remote crash or shutdown of bridge-related unmodified node software.
- Fast validation: Send malformed or oversized locally generated bridge inputs and observe whether the node exits, panics, or wedges.
