### Title
Tenure-start duplicate-block invariant (`DuplicateBlockFound`) is enforced only at proposal time and is not re-checked at signing, letting a miner get two signed tenure-start blocks for one tenure - (File: stacks-signer/src/v0/signer.rs, stacks-signer/src/chainstate/v1.rs)

### Summary
The "one signed tenure-start block per tenure" invariant is implemented as a single check, `validate_tenure_change_payload`'s `DuplicateBlockFound` rejection, and this check runs **only** at proposal arrival (inside `check_proposal`). It is deliberately not re-run at `handle_block_validate_ok` or at the pre-commit→signature stage, per the codebase's own documentation. The intended backstop for the signing stage, the "own-tenure conflict guard" inside `handle_block_pre_commit`, relies on `get_signed_conflicts(chain_length, …)`, which only returns already-signed blocks whose height is **at or above** the new proposal's `chain_length`. A second tenure-start (`TenureChangeCause::BlockFound`) proposal for the *same tenure* but at a **higher** `chain_length` than the already-signed first block will not appear in that conflict set, so the backstop silently does not fire, and the signature-time re-check that the docs promise ("relies on section 5's own-tenure conflict guard to cover the same ground") does not actually cover this case.

### Finding Description
`stacks-signer/src/chainstate/v1.rs` implements the tenure-uniqueness rule: [1](#0-0) 
This is the only place `DuplicateBlockFound` is raised for a second tenure-start block in a tenure, and it is invoked from `check_proposal`, which the docs confirm runs at proposal arrival only, not again at validate-ok or at signing time: [2](#0-1) 

The designed backstop at the moment a signature is actually produced is the "own-tenure conflict guard" in `handle_block_pre_commit`: [3](#0-2) 
and the same-tenure branch that follows it: [4](#0-3) 

Per the documentation's own description of this flow, `get_signed_conflicts` returns "signed conflicts at height ≥ h, in ANY tenure": [5](#0-4) 
and the log emitted when this guard fires explicitly says "same or higher height": [6](#0-5) 

This means the height-based conflict lookup is asymmetric: it can only catch a second block whose height is **≤** an already-signed conflict's height (i.e., it queries for signed blocks at or above the *new* proposal's chain_length). If a miner proposes a second tenure-start block for the same tenure with a strictly **higher** `chain_length` than the first (already signed) tenure-start block, the already-signed block sits *below* the new block's height and is never returned by `get_signed_conflicts`. Both `check_block_against_signer_db_state` (the tenure-change-confirms-parent check, which is keyed off the *parent* tenure for tenure-change blocks, not the current one) and the own-tenure conflict guard are therefore blind to this specific case, and the only check designed to prevent it (`DuplicateBlockFound`) already fired-and-forgot at proposal time.

This mirrors the Thorn `whenNotPaused` bug class exactly: a safety invariant (`Pausable`/`DuplicateBlockFound`) is applied consistently on one code path (`SwapThreePoolDeployer`/proposal-time `check_proposal`) but is missing from a sibling entry point that can independently trigger the same sensitive action (`StableSwapLPFactory.createSwapLP`/the signing-time pre-commit path), and the "obvious" secondary guard that exists on that sibling path does not actually re-implement the same equality test (it's scoped by height rather than by tenure).

### Impact Explanation
If a single miner (during their own tenure slot — no majority of signers required) proposes two tenure-start ("BlockFound") blocks for the same tenure at different heights within the async-validation race window described by the codebase's own `async_sibling_validation` tests, the signer set can end up producing valid signatures over **two different tenure-start blocks for the same tenure** — a conflicting/non-canonical pair of blocks both bearing the group's signature. This breaks the "one signed block per tenure-start" equality that `DuplicateBlockFound` exists to enforce, which is exactly the Critical-tier outcome called out in the rules ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
Triggerable entirely by the block-proposing miner for their own slot plus normal signer/gossip processing — no other signer's key or majority collusion is needed. It requires only that the miner race two tenure-start proposals with different heights within the existing async-validation timing window that the codebase's own tests (`signer_refuses_to_sign_second_sibling_tenure_start`, `stale_sibling_still_refused_when_canonical_tip_at_height`) already demonstrate is exploitable for the same-height case; using a different height for the second proposal removes even the coverage those tests validate.

### Recommendation
Re-run the tenure-uniqueness check (equivalent to `DuplicateBlockFound`) at the pre-commit/signing stage in `handle_block_pre_commit` (and ideally also in `handle_block_validate_ok`), scoped by `consensus_hash` (tenure) rather than solely by `chain_length`/height, so that `get_signed_conflicts` (or an additional query) can detect a second signed tenure-start block in the same tenure regardless of whether its height is higher or lower than the first. Alternatively, extend `get_signed_conflicts` to also return same-tenure conflicts irrespective of height ordering, and treat any such conflict as blocking signature production the same way the "no confirms_expected_parent" chainstate check does.

### Proof of Concept
1. Miner controls a tenure and proposes tenure-start block `A` (`chain_length = 10`, `TenureChangeCause::BlockFound`) for tenure `T`.
2. Before `A` is fully signed/recorded by all signers (i.e., within the async node-validation window used by the existing sibling tests, e.g. `run_sibling_scenario`/`async_sibling_validation` in `stacks-signer/src/v0/tests.rs`), the miner proposes a second tenure-start block `B` for the same tenure `T`, but with `chain_length = 11` (higher than `A`).
3. `check_proposal` for `B` calls `validate_tenure_change_payload`, which checks `get_last_globally_accepted_block`/`get_last_signed_block` for tenure `T` at the time of `B`'s screening; if `A` has not yet reached that state locally (still pending validation), `B` passes proposal-time checks (`DuplicateBlockFound` not raised).
4. `A` finishes validation first and is signed (`mark_locally_accepted`), reaching `LocallyAccepted`/`GloballyAccepted` at height 10.
5. `B` finishes validation and proceeds to `handle_block_pre_commit`. `get_signed_conflicts(11, hash_B)` is queried; since `A`'s height (10) is below 11, `A` is not returned as a conflict.
6. The chainstate re-check (`check_block_against_signer_db_state`) for `B`, being a tenure-change block, only checks the *parent* tenure's tip via `check_tenure_change_confirms_parent`, not the current tenure `T`'s state — so it does not catch that `T` already has a signed block.
7. `B` crosses the pre-commit threshold and is signed via `mark_locally_accepted`, producing two signed, conflicting tenure-start blocks (`A` and `B`) for tenure `T`.

I was not able to inspect the exact SQL/definition of `get_signed_conflicts` in `stacks-signer/src/signerdb.rs` (ran out of tool budget), so the precise height-comparison semantics are inferred from the mermaid diagram annotation ("signed conflicts at height ≥ h, in ANY tenure") and the accompanying log message text in `stacks-signer/src/v0/signer.rs:1412-1421`, both of which are strong but indirect evidence rather than a direct read of the query.

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

**File:** docs/signer-flows.md (L248-250)
```markdown
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> CONF["signed conflicts at height ≥ h,<br/>in ANY tenure<br/>get_signed_conflicts"]
```

**File:** docs/signer-flows.md (L425-437)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
```rust
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
        if let Some(conflict) = conflicts.iter().find(|conflict| {
            conflict.last_endorsed > freshness_cutoff
                && !self.reorg_permit_stands(stacks_client, conflict)
                && self.conflict_still_blocks(
                    stacks_client,
                    conflict,
                    block_info.block.header.chain_length,
                )
        }) {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but we have recently signed or accepted a different block at the same or higher height. Refusing to sign.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "conflicting_signer_signature_hash" => %conflict.signer_signature_hash,
                "conflicting_block_height" => conflict.stacks_height,
                "conflicting_consensus_hash" => %conflict.consensus_hash,
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1457)
```rust
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
```
