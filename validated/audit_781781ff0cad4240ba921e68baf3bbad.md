### Title
V1 tenure-change duplicate-block guard checks only globally-accepted state, letting a signer sign a conflicting tenure-start block for the same tenure - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
The M-01 report's root cause is a check that gates on state ("has the pre-condition already happened?") that an attacker can manipulate before the check runs, so the guard fails to catch the case it was designed for. The v1 signer chainstate has the same class of bug: `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` only rejects a competing tenure-start block as `DuplicateBlockFound` if a block in that tenure has already reached `GloballyAccepted` state, whereas the v2 implementation was fixed to also cover the `LocallyAccepted`/signed-but-not-yet-global window.

### Finding Description
In `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` (lines 505-518), the duplicate-tenure-start check is:

```rust
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)
    ...
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ...
    return Err(RejectReason::DuplicateBlockFound);
}
``` [1](#0-0) 

This is queried by `get_last_globally_accepted_block` only — a block this signer has locally signed/accepted for the tenure but that has not yet crossed the global signature threshold is invisible to this check.

The equivalent v2 function `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v2.rs` was written (or fixed) to use `get_last_signed_block` instead, which covers both locally- and globally-accepted (i.e., any block this signer has already signed) blocks in the tenure:

```rust
let last_in_current_tenure = signer_db
    .get_last_signed_block(&block.header.consensus_hash)
    ...
``` [2](#0-1) 

The project's own documentation confirms this divergence is deliberate/known: "`validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we have already accepted a block in the tenure a tenure-change block is starting. v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1 counts only globally accepted ones (`get_last_globally_accepted_block`)." [3](#0-2) 

There is also a regression test proving the v1-style behavior was a real bug when it existed in v2's own history: `check_tenure_change_rejects_when_locally_accepted_block_exists` in `stacks-signer/src/chainstate/tests/v2.rs`, whose comment explicitly states "Before the fix, this would have incorrectly passed because `get_last_globally_accepted_block` would not find the locally-accepted block." [4](#0-3) 

v1 chainstate is not dead code: it is still selected for any signer running below the `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` protocol version, via `SortitionStateVersion::from_protocol_version`. [5](#0-4) 

**Attack path (analog of the front-running equality bypass in the report):**
1. Miner proposes tenure-start block A for tenure T. A gathers enough signatures to be `LocallyAccepted`/signed by this signer (and possibly by enough signers to be a real candidate) but has not yet crossed the network-wide threshold recorded as `GloballyAccepted` in this signer's local view (e.g., due to network delay, a race, or the miner intentionally not broadcasting enough acceptances yet).
2. Before A becomes globally accepted from this signer's perspective, the miner (or an attacker who controls block proposal for the tenure, e.g., after a stolen/associated miner key or a byzantine miner) proposes a second, conflicting tenure-start block B for the same tenure T (same `prev_tenure_consensus_hash`, different contents/parent choice within what the check allows).
3. `validate_tenure_change_payload` (v1) calls `get_last_globally_accepted_block(&block.header.consensus_hash)`, which returns `None` because A is only locally accepted, not yet globally accepted from this signer's point of view.
4. The duplicate check is bypassed; B passes tenure-change validation and can proceed to be signed by this signer, producing two signer-signed, conflicting tenure-start blocks (A and B) for the same tenure.

This breaks the "one-per-tenure-start" equality/invariant that the duplicate check exists to enforce, analogous to how the report's balance-equality check could be defeated by manipulating the pre-check state (front-running the transfer). Here the state being raced is the local acceptance→global acceptance transition rather than a token balance, but the mechanism—an equality/gate check keyed to a mutable, attacker-influenceable precondition—is the same bug class.

### Impact Explanation
If exploited, a v1-protocol signer can be induced to sign two different blocks that both start the same tenure (A and B), which is exactly the "signer signing a conflicting block" class of impact called out as Critical in this scan's rules: it produces a signature over a block that conflicts with another block this same signer already signed for the same tenure, undermining the safety guarantee that a signer's signature set is free of same-tenure conflicts. This can contribute to chain forks/equivocation-style situations at the tenure-start boundary.

### Likelihood Explanation
Low-to-moderate. It requires:
- The signer to be running the v1 (pre-`GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`) chainstate path, so it is not universally reachable in an already-fully-upgraded network.
- A window where a block is locally-but-not-globally-accepted from this specific signer's perspective (achievable by a miner via timing, e.g., proposing the second competing block quickly after the first, before it is globally confirmed) — no majority-of-signers collusion is required, consistent with a single miner/proposer-triggerable analog similar to the front-running miner in the original report.

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `get_last_signed_block` (as v2 already does) instead of `get_last_globally_accepted_block`, so that any block this signer has already signed (locally or globally accepted) for the tenure is treated as blocking a competing tenure-start proposal:

```diff
- let last_in_current_tenure = signer_db
-     .get_last_globally_accepted_block(&block.header.consensus_hash)
+ let last_in_current_tenure = signer_db
+     .get_last_signed_block(&block.header.consensus_hash)
```

### Proof of Concept
1. Configure a signer running with protocol version `< GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` so `SortitionStateVersion::from_protocol_version` selects the `V1` chainstate path. [5](#0-4) 
2. Propose tenure-start block A for tenure T; have the signer locally accept/sign A but do not let it reach `GloballyAccepted` state in this signer's `SignerDb` (e.g. delay broadcasting enough peer acceptances).
3. Immediately propose a second, conflicting tenure-start block B for the same tenure T (same `prev_tenure_consensus_hash`).
4. Observe that `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` calls `get_last_globally_accepted_block`, finds `None` (since A is only locally accepted), and does not return `RejectReason::DuplicateBlockFound`, allowing B to pass this check and potentially be signed — reproducing, in the v1 path, the exact scenario the v2 regression test `check_tenure_change_rejects_when_locally_accepted_block_exists` was written to prevent for v2. [6](#0-5)

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L505-518)
```rust
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L344-357)
```rust
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** docs/signer-flows.md (L428-431)
```markdown
- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L755-849)
```rust
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
    let MockServerClient {
        server,
        client: stacks_client,
        config: _,
    } = MockServerClient::new();
    let rand_int = server.local_addr().unwrap().port();

    let (_stacks_client, mut signer_db, block_sk, mut block, cur_sortition, _, sortitions_view) =
        setup_test_environment(&format!("{}_{rand_int}", function_name!()));

    // Set up the block in the current tenure
    block.header.consensus_hash = cur_sortition.data.consensus_hash.clone();
    let parent_block_header = make_parent_header_meta(&block_sk, &mut block);
    let response = crate::client::tests::build_get_tenure_tip_response(&parent_block_header);

    // Insert a locally-accepted block in the same tenure (same consensus_hash).
    // This simulates a miner's first tenure-start block that the signer has
    // locally accepted but that hasn't yet gathered enough signatures to be
    // globally accepted. In practice this block would contain a tenure-change
    // and coinbase tx, but we omit them here because `get_last_accepted_block`
    // only queries by consensus_hash and block state — the block's transactions
    // are irrelevant to the duplicate check.
    let existing_block_proposal = BlockProposal {
        block: NakamotoBlock::new(
            NakamotoBlockHeader {
                version: 1,
                chain_length: 10,
                burn_spent: 10,
                consensus_hash: cur_sortition.data.consensus_hash.clone(),
                parent_block_id: StacksBlockId([0; 32]),
                tx_merkle_root: Sha512Trunc256Sum([0; 32]),
                state_index_root: TrieHash([0; 32]),
                timestamp: 11,
                miner_signature: MessageSignature::empty(),
                signer_signature: vec![],
                pox_treatment: BitVec::ones(1).unwrap(),
                problematic_txs: vec![],
            },
            vec![],
        ),
        burn_height: 2,
        reward_cycle: 1,
        block_proposal_data: BlockProposalData::empty(),
    };
    let mut existing_block_info = BlockInfo::from(existing_block_proposal);
    existing_block_info.mark_locally_accepted(false).unwrap();
    signer_db.insert_block(&existing_block_info).unwrap();

    // Now build a *second* tenure-start block proposal for the same tenure.
    // This simulates the miner attempting to replace their first block (e.g.,
    // with different transactions). The tenure change tx must have
    // cause=BlockFound with a coinbase to be recognized as a tenure-start block.
    let tenure_change_payload = TenureChangePayload {
        tenure_consensus_hash: cur_sortition.data.consensus_hash.clone(),
        prev_tenure_consensus_hash: cur_sortition.data.parent_tenure_id.clone(),
        burn_view_consensus_hash: cur_sortition.data.consensus_hash.clone(),
        previous_tenure_end: block.header.parent_block_id.clone(),
        previous_tenure_blocks: 1,
        cause: TenureChangeCause::BlockFound,
        pubkey_hash: Hash160::from_node_public_key(&StacksPublicKey::from_private(&block_sk)),
    };
    let tenure_change_tx = make_tenure_change_tx(tenure_change_payload);
    let coinbase_tx = StacksTransaction::new(
        TransactionVersion::Testnet,
        TransactionAuth::Standard(TransactionSpendingCondition::new_initial_sighash()),
        TransactionPayload::Coinbase(CoinbasePayload([0; 32]), None, Some(VRFProof::empty())),
    );
    *block.executed_and_skipped_txs_mut() = vec![tenure_change_tx, coinbase_tx];
    block.header.sign_miner(&block_sk).unwrap();

    let exit_flag = Arc::new(AtomicBool::new(false));
    let moved_exit_flag = exit_flag.clone();

    let serve = std::thread::spawn(move || {
        crate::client::tests::write_response_nonblockinig(
            &server,
            response.as_bytes(),
            moved_exit_flag,
        );
    });

    let result = sortitions_view.check_proposal(&stacks_client, &mut signer_db, &block);

    exit_flag.store(true, Ordering::SeqCst);
    serve.join().unwrap();

    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
```

**File:** stacks-signer/src/chainstate/mod.rs (L532-540)
```rust
impl SortitionStateVersion {
    /// Convert the protocol version to a sortition state version
    pub fn from_protocol_version(version: u64) -> Self {
        if version < GLOBAL_SIGNER_STATE_ACTIVATION_VERSION {
            Self::V1
        } else {
            Self::V2
        }
    }
```
