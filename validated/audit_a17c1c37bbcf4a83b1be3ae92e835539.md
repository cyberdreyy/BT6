### Title
`generated::Message → VersionedMessage` conversion collapses V1 messages into V0 and silently drops `TransactionConfig` - ([File: storage-proto/src/convert.rs])

### Summary
The `impl From<generated::Message> for VersionedMessage` (storage-proto/src/convert.rs:405-440) only branches on the `versioned` boolean and never reconstructs `VersionedMessage::V1`, even though `generated::Message` carries a `config: Option<TransactionConfig>` field that is populated only for v1 messages (storage-proto/src/convert.rs:368-393). Any transaction whose message is `VersionedMessage::V1` (a real, supported message type once `enable_tx_v1` is active, per `runtime/src/bank.rs` and `runtime-transaction/src/runtime_transaction/transaction_view.rs:244-259`) will, on read-back through this `From` impl, be reconstructed as `VersionedMessage::V0` with the `TransactionConfig` (priority_fee, compute_unit_limit, loaded_accounts_data_size_limit, heap_size) discarded.

### Finding Description
Write path: `impl From<v1::Message> for generated::Message` (storage-proto/src/convert.rs:368-393) sets `versioned: true` and `config: Some(generated::TransactionConfig{ priority_fee, compute_unit_limit, loaded_accounts_data_size_limit, heap_size })`. This is the only variant that populates `config`; `LegacyMessage`/`v0::Message` conversions leave `config: None` (convert.rs:337, 363).

Read path: `impl From<generated::Message> for VersionedMessage` (convert.rs:405-440) does:
```rust
if !value.versioned {
    Self::Legacy(LegacyMessage { .. })
} else {
    Self::V0(v0::Message { .. })   // config is never read
}
```
There is no third branch that inspects `value.config` to reconstruct `VersionedMessage::V1`. The `config` field is read nowhere in this function, and `header`, `account_keys`, `recent_blockhash`, `instructions`, and `address_table_lookups` are populated for a `v0::Message` struct, which has no `config` member at all — the `TransactionConfig` data is structurally unrepresentable in the target variant.

This code path is exercised by any consumer that serializes a transaction to the storage protobuf representation (e.g., bigtable long-term storage in `storage-bigtable/src/lib.rs`) and later deserializes it back for `getTransaction`/`getConfirmedTransaction`-style RPC responses. Once a v1 transaction is confirmed on-chain (a normal, unprivileged client action — submitting a transaction is not privileged), it will be persisted with `versioned: true, config: Some(...)`. Any subsequent single `getTransaction` RPC call that reads that entry back through this `From` impl reconstructs a `VersionedMessage::V0` instead of `V1`, permanently and silently losing the original transaction's priority fee, compute unit limit, loaded-accounts-data-size limit, and heap size — a genuine misrepresentation of the on-chain resource limits reported to the client, with no error, panic, or warning.

### Impact Explanation
Scoped impact: a client reading a previously-v1 transaction via `getTransaction` (or any equivalent read of the stored/serialized confirmed transaction) receives a `VersionedMessage::V0` transaction object lacking the correct message version and its `TransactionConfig` fields entirely dropped. This is a decoder/parsing correctness bug — "wrong data returned" from a query — matching the "misreporting" / decoder-misrepresentation category the audit explicitly asks about (parsed output must faithfully represent the raw instruction/message). It does not crash the validator or mutate consensus state; the impact is limited to incorrect data being returned to RPC clients reading that specific transaction.

### Likelihood Explanation
Preconditions: (1) `enable_tx_v1` feature must be active so a V1 transaction can be included on-chain (unprivileged submission of an ordinary transaction, no special access needed); (2) the transaction must be persisted through the storage-proto `generated::Message` representation (e.g., bigtable ledger storage used for historical `getTransaction`); (3) a client issues a single `getTransaction` call for that signature. All of these are attacker/observer-achievable with unprivileged, single-call RPC access — no validator/leader/gossip control, no mocked paths, and no direct store mutation is required; the bug is deterministic and 100% reproducible for any v1 transaction once feature-enabled.

### Recommendation
Add a third branch in `impl From<generated::Message> for VersionedMessage` that checks `value.config.is_some()` (or a dedicated version discriminant instead of the boolean `versioned` flag) and reconstructs `VersionedMessage::V1(v1::Message { header, config: value.config.unwrap().into(), lifetime_specifier: recent_blockhash, account_keys, instructions })` when `config` is present, mirroring the write-side `From<v1::Message> for generated::Message` impl. Replace the boolean `versioned` field's semantics (or add an explicit `version` enum) in the protobuf schema (`storage-proto/proto/confirmed_block.proto`) to unambiguously distinguish Legacy/V0/V1 rather than inferring it from `versioned` + presence of `config`.

### Proof of Concept
```rust
// storage-proto/src/convert.rs (add to test module)
#[test]
fn v1_message_round_trip_preserves_config() {
    let original = v1::Message {
        header: MessageHeader {
            num_required_signatures: 1,
            num_readonly_signed_accounts: 0,
            num_readonly_unsigned_accounts: 1,
        },
        config: v1::TransactionConfig {
            priority_fee: 42,
            compute_unit_limit: 123_456,
            loaded_accounts_data_size_limit: 456_789,
            heap_size: 65_536,
        },
        lifetime_specifier: Hash::new_unique(),
        account_keys: vec![Pubkey::new_unique(), Pubkey::new_unique()],
        instructions: vec![CompiledInstruction {
            program_id_index: 1,
            accounts: vec![0],
            data: vec![],
        }],
    };
    let versioned = VersionedMessage::V1(original.clone());

    // Round trip: VersionedMessage -> generated::Message -> VersionedMessage
    let generated: generated::Message = versioned.clone().into();
    assert!(generated.config.is_some(), "config should be persisted");

    let reconstructed: VersionedMessage = generated.into();

    // BUG: this assertion fails today because reconstructed is V0, not V1,
    // and priority_fee/compute_unit_limit/etc. are silently dropped.
    match reconstructed {
        VersionedMessage::V1(msg) => {
            assert_eq!(msg.config.priority_fee, 42);
            assert_eq!(msg.config.compute_unit_limit, 123_456);
            assert_eq!(msg.config.loaded_accounts_data_size_limit, 456_789);
            assert_eq!(msg.config.heap_size, 65_536);
        }
        other => panic!("expected V1, got {other:?} - TransactionConfig was discarded"),
    }
}
```
Expected result today: the test fails because `reconstructed` is `VersionedMessage::V0` (missing `config` entirely), demonstrating the silent loss of `TransactionConfig` and the wrong-variant misrepresentation on read-back.