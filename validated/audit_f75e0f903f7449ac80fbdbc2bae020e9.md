### Title
Loose "Simple Vote" Transaction Classification Allows Unauthorized Entry into the Reserved Vote-Only Fast Path - ([File: perf/src/sigverify.rs])

### Summary
The bug-class described in the external report is "a reserved classification value (fee tier 1) that is supposed to be auto-derived from a strict condition (`_isVirtualWallet`) can instead be manually asserted, causing conflicts in downstream logic that trusts the classification." The direct analog in `agave` is the `is_simple_vote_tx` classification computed in `perf/src/sigverify.rs::is_simple_vote_transaction_view`. This classification is supposed to identify genuine validator vote transactions (reserved for the consensus-critical vote lane and exempted from normal fee/priority accounting), but the check only inspects transaction *shape* (signature count, message version, single instruction, program-id match) and never validates that the instruction data actually deserializes into a real vote instruction. Any ordinary user can therefore manually construct a transaction that is classified as a "simple vote" without being one.

### Finding Description
`is_simple_vote_transaction_view` in `perf/src/sigverify.rs` decides whether a transaction is a "simple vote": [1](#0-0) 

It only checks:
1. `num_signatures() <= 2`
2. legacy message version
3. exactly one instruction
4. the instruction's `program_id == solana_sdk_ids::vote::id()`

It never calls into `limited_deserialize::<VoteInstruction>` or checks `is_single_vote_state_update()`, unlike the stricter `vote_parser::is_valid_vote_only_transaction`, which does perform that content validation: [2](#0-1) 

The looser flag is set on the packet as `PacketFlags::SIMPLE_VOTE_TX` directly in `verify_packet`: [3](#0-2) 

This packet-level flag is subsequently trusted, not re-derived, as it flows through the pipeline: it is translated into `tpu_message_flags::IS_SIMPLE_VOTE` in the TPU ingest path, [4](#0-3) 
and used directly as an `Option<bool>` override when constructing a `RuntimeTransaction`, bypassing any re-check of vote validity: [5](#0-4) 

Once cached as `is_simple_vote_transaction()` on the transaction meta, downstream consumers unconditionally trust it — e.g. the prioritization-fee cache explicitly excludes anything flagged as a simple vote from fee-market accounting: [6](#0-5) 

`sigverify::verify_packet` also accepts a `reject_non_vote` parameter used to gate admission to the TPU-vote ingestion port, meaning the *same* loose shape-only check is the sole gatekeeper for the reserved consensus-vote ingest lane: [7](#0-6) 

Because the classification never validates the actual `VoteInstruction` payload, any ordinary user (not just validators) can craft a legacy, single-instruction, ≤2-signature transaction that targets `vote::id()` with arbitrary/garbage instruction data (e.g., a malformed payload, or a legitimate but non-state-update vote instruction such as `Authorize`) and have it accepted as a "simple vote" throughout the pipeline — exactly analogous to manually forcing the reserved `VW_FEE_TIER=1` value onto an account that was never actually a virtual wallet.

### Impact Explanation
This misclassification is reachable from any unprivileged transaction sender via the standard TPU/QUIC ingest path, requiring no elevated privilege:
- It bypasses prioritization-fee accounting (`PrioritizationFeeCache::update`), corrupting the minimum-fee signal reported to the network/RPC and to leaders' own fee-market decisions.
- It gains admission to the TPU-vote ingest port/lane, which is architecturally reserved and prioritized for consensus-critical validator votes (gated only by `reject_non_vote` using this same loose check), allowing non-vote traffic to consume vote-lane ingest capacity — a form of ingest starvation against the reserved fast path used to keep consensus votes flowing under load.
- It is exempted from cost/fee treatment that assumes the transaction is a genuine validator vote, creating inconsistencies between how the transaction is billed/prioritized and its actual on-chain effect once it fails execution (e.g. as a bad `VoteInstruction` deserialize error) or executes a non-vote-state vote-program instruction.

### Likelihood Explanation
High likelihood of exploitability: constructing a legacy, single-instruction, ≤2-signature transaction whose sole instruction targets the vote program with any instruction data is trivial and requires no special access — no valid vote account, no stake, and no leader/validator role. The check is purely syntactic/shape-based and is evaluated identically for all packet sources (TPU, TPU-vote, gossip-forwarded).

### Recommendation
- Tighten `is_simple_vote_transaction_view` (and any equivalent fast classification used for admission control) to require successful `limited_deserialize::<VoteInstruction>` and `is_single_vote_state_update()` — mirroring the stricter check already implemented in `vote_parser::is_valid_vote_only_transaction` — before setting `PacketFlags::SIMPLE_VOTE_TX` / `IS_SIMPLE_VOTE`.
- Ensure the reserved vote-only ingest lane (`reject_non_vote` gating) and prioritization-fee exemption both rely on the stricter, content-validated classification rather than the shape-only heuristic.
- Add tests asserting that transactions targeting the vote program with non-vote-state-update instructions (e.g., `Authorize`) or malformed instruction data are NOT classified as `is_simple_vote_tx` and are rejected from the vote-only ingest port.

### Proof of Concept
1. Construct a legacy `Transaction` with exactly one instruction whose `program_id` is `solana_sdk_ids::vote::id()`, with 1–2 signatures, and with arbitrary instruction `data` that is not a valid serialized `VoteInstruction::TowerSync`/`UpdateVoteState` (e.g., raw bytes `[1,2,3]`, or a valid-but-non-state-update instruction such as `VoteInstruction::Authorize`).
2. Submit the packet to the validator's TPU or TPU-vote port.
3. Observe in `perf/src/sigverify.rs::verify_packet` that `is_simple_vote_transaction_view` returns `true` (per the checks in [1](#0-0) ), setting `PacketFlags::SIMPLE_VOTE_TX`, and the packet passes `reject_non_vote` gating for the vote port.
4. Observe downstream that `RuntimeTransaction::is_simple_vote_transaction()` returns `true` for this transaction (via the trusted `Some(is_simple_vote_tx)` override in `sdk_transactions.rs`), causing `PrioritizationFeeCache::update` to skip fee-market accounting for it (`runtime/src/prioritization_fee_cache.rs:219`), despite the transaction not being a genuine consensus vote.

Note: I was unable to fully inspect `core/src/sigverify.rs` (only match counts for `reject_non_vote` were returned, not file contents) before the tool budget ended, so the exact call-site wiring that ties `reject_non_vote` to the TPU-vote port could not be fully confirmed by direct code reading — this should be verified by a follow-up session with full file access.

### Citations

**File:** perf/src/sigverify.rs (L17-63)
```rust
/// Returns true if the signature on the packet verifies.
/// Caller must do packet.set_discard(true) if this returns false.
#[must_use]
fn verify_packet(packet: &mut PacketRefMut, reject_non_vote: bool, enable_tx_v1: bool) -> bool {
    // If this packet was already marked as discard, drop it
    if packet.meta().discard() {
        return false;
    }

    let Some(data) = packet.data(..) else {
        return false;
    };

    let (is_simple_vote_tx, verified) = {
        let Ok(view) = SanitizedTransactionView::try_new_sanitized(data, &sanitize_config()) else {
            return false;
        };

        if !enable_tx_v1 && matches!(view.version(), TransactionVersion::V1) {
            return false;
        }

        let is_simple_vote_tx = is_simple_vote_transaction_view(&view);
        if reject_non_vote && !is_simple_vote_tx {
            (is_simple_vote_tx, false)
        } else {
            let signatures = view.signatures();
            if signatures.is_empty() {
                (is_simple_vote_tx, false)
            } else {
                let message = view.message_data();
                let static_account_keys = view.static_account_keys();
                let verified = signatures
                    .iter()
                    .zip(static_account_keys.iter())
                    .all(|(signature, pubkey)| signature.verify(pubkey.as_ref(), message));
                (is_simple_vote_tx, verified)
            }
        }
    };

    if is_simple_vote_tx {
        packet.meta_mut().flags |= PacketFlags::SIMPLE_VOTE_TX;
    }

    verified
}
```

**File:** perf/src/sigverify.rs (L76-106)
```rust
fn is_simple_vote_transaction_view<D: TransactionData>(view: &SanitizedTransactionView<D>) -> bool {
    // vote could have 1 or 2 sigs; zero sig has already been excluded by sanitization.
    if view.num_signatures() > 2 {
        return false;
    }

    // simple vote should only be legacy message
    if !matches!(view.version(), TransactionVersion::Legacy) {
        return false;
    }

    // skip if has more than 1 instruction
    if view.num_instructions() != 1 {
        return false;
    }

    let mut instructions = view.instructions_iter();
    let Some(instruction) = instructions.next() else {
        return false;
    };
    if instructions.next().is_some() {
        return false;
    }

    let program_id_index = usize::from(instruction.program_id_index);
    let Some(program_id) = view.static_account_keys().get(program_id_index) else {
        return false;
    };

    *program_id == solana_sdk_ids::vote::id()
}
```

**File:** vote/src/vote_parser.rs (L10-33)
```rust
/// Check if a transaction is a valid vote-only transaction.
/// A valid vote-only transaction must:
/// 1. Have exactly one instruction
/// 2. That instruction must be to the vote program
/// 3. That instruction must be a single vote state update (UpdateVoteState, TowerSync, etc.)
pub fn is_valid_vote_only_transaction(tx: &impl SVMTransaction) -> bool {
    let mut instructions = tx.program_instructions_iter();

    let Some((program_id, instruction)) = instructions.next() else {
        return false;
    };

    if instructions.next().is_some() {
        return false;
    }

    if !solana_sdk_ids::vote::check_id(program_id) {
        return false;
    }

    limited_deserialize::<VoteInstruction>(instruction.data, solana_packet::PACKET_DATA_SIZE as u64)
        .map(|ix| ix.is_single_vote_state_update())
        .unwrap_or(false)
}
```

**File:** core/src/banking_stage/tpu_to_pack.rs (L173-187)
```rust
fn flags_from_meta(flags: PacketFlags) -> u8 {
    let mut tpu_message_flags = 0;

    if flags.contains(PacketFlags::SIMPLE_VOTE_TX) {
        tpu_message_flags |= tpu_message_flags::IS_SIMPLE_VOTE;
    }
    if flags.contains(PacketFlags::FORWARDED) {
        tpu_message_flags |= tpu_message_flags::FORWARDED;
    }
    if flags.contains(PacketFlags::FROM_STAKED_NODE) {
        tpu_message_flags |= tpu_message_flags::FROM_STAKED_NODE;
    }

    tpu_message_flags
}
```

**File:** runtime-transaction/src/runtime_transaction/sdk_transactions.rs (L22-34)
```rust
impl RuntimeTransaction<SanitizedVersionedTransaction> {
    pub fn try_from(
        sanitized_versioned_tx: SanitizedVersionedTransaction,
        message_hash: MessageHash,
        is_simple_vote_tx: Option<bool>,
    ) -> Result<Self> {
        let message_hash = match message_hash {
            MessageHash::Precomputed(hash) => hash,
            MessageHash::Compute => sanitized_versioned_tx.get_message().message.hash(),
        };
        let is_simple_vote_tx = is_simple_vote_tx
            .unwrap_or_else(|| is_simple_vote_transaction(&sanitized_versioned_tx));

```

**File:** runtime/src/prioritization_fee_cache.rs (L216-221)
```rust
            for sanitized_transaction in txs {
                // Vote transactions are not prioritized, therefore they are excluded from
                // updating fee_cache.
                if sanitized_transaction.is_simple_vote_transaction() {
                    continue;
                }
```
