# Q0134: parse_group_member_pointer_instruction confuses account types or owners (group_member_pointer.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `parse_group_member_pointer_instruction` in `transaction-status/src/parse_token/extension/group_member_pointer.rs` with a truncated or over-long encoding whose declared length disagrees with its real length, and have `parse_group_member_pointer_instruction` deserialize an account of the wrong owner or discriminant as valid, so that the invariant "`parse_group_member_pointer_instruction` validates owner and discriminant before trusting deserialized content." breaks and the result is Loss of Funds?

## Target
- File/function: `transaction-status/src/parse_token/extension/group_member_pointer.rs` -> `parse_group_member_pointer_instruction()` (around line 9)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: a truncated or over-long encoding whose declared length disagrees with its real length
- Exploit idea: Pass an account of a different type/owner that `parse_group_member_pointer_instruction` deserializes successfully, so a later check trusts the wrong discriminant.
- Invariant to test: `parse_group_member_pointer_instruction` validates owner and discriminant before trusting deserialized content.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Feed every other account layout into `parse_group_member_pointer_instruction` and assert each is rejected rather than silently reinterpreted.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged client can receive balances, transaction status, or account data attributed to the wrong account, slot, fork, or commitment level, so wallets and exchanges credit or release value on state that is not final.
