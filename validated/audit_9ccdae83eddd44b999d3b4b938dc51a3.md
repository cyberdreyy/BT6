### Title
Stale pre-commit signed after signer's own miner-view capitulates to a new tenure - (File: `stacks-signer/src/v0/signer.rs`, `stacks-signer/src/chainstate/v2.rs`, `stacks-signer/src/v0/signer_state.rs`)

### Summary
The `updateLoanParams`/`removeCollateral` bug class is "a privileged party can change a validated parameter after the check that relied on it has already run, so a stale, narrower re-check lets a state transition through that would have been rejected had the full check re-run." The signer analog is the split between the **full proposal check** (`GlobalStateView::check_proposal` / `SortitionsView::check_proposal`), which verifies the block's `consensus_hash` and miner pubkey hash against the signer's *current* `MinerState::ActiveMiner{tenure_id, current_miner_pkh, ...}`, and the **narrower re-check** (`check_block_against_signer_db_state`) that runs at validate-ok time and again at pre-commit-threshold time. The narrow re-check only re-verifies tenure-continuity/conflict conditions (`check_latest_block_in_tenure`, `get_signed_conflicts`), not the consensus-hash/pubkey-hash equality that ties the block to "the miner the signer currently believes is active."

### Finding Description
`GlobalStateView::check_proposal` performs the binding checks exactly once, at fresh-proposal time: [1](#0-0) 

Between that first check and the moment the pre-commit weight crosses 70% and a signature is actually produced, the signer's local view of "who the current miner is" (`SignerStateMachine::current_miner`, containing `tenure_id`/`current_miner_pkh`/`parent_tenure_last_block`) is not frozen — it is continuously updated by `capitulate_viewpoint`/`capitulate_miner_view`, which adopts a new miner viewpoint purely from gossiped `StateMachineUpdate` weight tallies, independent of the specific block being pre-committed: [2](#0-1) [3](#0-2) 

The documentation for this codebase states explicitly that the consensus-hash/pubkey-hash/bitvec/tenure-extend checks performed inside `check_proposal` are **not** re-run at validate-ok or at signing time; only the tenure-continuity/duplicate-block check is: [4](#0-3) 

And the pre-commit → signature path (section 5 of the same doc) confirms that the re-check performed right before the irreversible signature is produced is `check_block_against_signer_db_state`, followed only by a *conflict* search (`get_signed_conflicts`, `reorg_permit_stands`, `conflict_still_blocks`) — never a re-verification that the block's `consensus_hash`/miner pubkey still matches the signer's *current* `current_miner`: [5](#0-4) 

Concretely: a miner proposes block `B` for tenure `T1` while the signer's `current_miner` is `ActiveMiner{tenure_id: T1, current_miner_pkh: P1, ...}`. `check_proposal` passes (consensus_hash == T1, pubkey == P1), the node validates OK, and the signer pre-commits to `B`. Before 70% pre-commit weight is reached, a new sortition/burn-block arrives (or peer gossip crosses its own weight threshold) and `capitulate_viewpoint`/`capitulate_miner_view` swings the signer's `current_miner` to `ActiveMiner{tenure_id: T2, current_miner_pkh: P2, ...}` — a *different* tenure than the one `B` belongs to, with **no signed block yet recorded in `T2`**, so `get_signed_conflicts` finds nothing to block on. When the pre-commit threshold for `B` is (still) reached from the peers who committed before the switch, the signer proceeds straight to `check_block_against_signer_db_state` and the conflict search in section 5, both of which are silent about the mismatch between `B`'s tenure/miner and the signer's now-current miner view, and the signer signs `B` — a block that is stale relative to its own belief of the canonical tenure, exactly as `removeCollateral()` in the original report succeeded because `ltvBPS` was changed out from under the invariant `rate * ltvBPS / BPS < amount` without that invariant being re-derived from the *current* parameter.

### Impact Explanation
This breaks the "approved-parent vs canonical" and "signed vs validated" equalities called out in the rules: the signer emits an irrevocable signature over a block whose tenure/miner identity no longer matches what the signer's own state machine has capitulated to as canonical. That is the Critical impact class: "a signer signing an invalid/non-canonical/conflicting block."

### Likelihood Explanation
This requires only the normal, protocol-legitimate sequence of events — a proposal, gossip-driven pre-commits, and a subsequent tenure/sortition change that causes `capitulate_miner_view` to swing before the pre-commit threshold is reached and before any block is signed in the new tenure (so `get_signed_conflicts` sees nothing to block on). No majority-controlled key or auth token is needed; it is a race between normal chain progress and the pre-commit accumulation window, which the doc itself acknowledges can span "time passes, the burn chain can fork, and another block may win the same slot."

### Recommendation
`check_block_against_signer_db_state` (and the pre-commit-threshold re-check in section 5) should re-verify the block's `consensus_hash` and miner pubkey hash against the signer's *current* `current_miner` state (not only via the conflict/duplicate-block heuristics), mirroring the check `GlobalStateView::check_proposal` already performs at proposal time, so a capitulated miner-view change is always sufficient by itself to block a stale pre-commit from crossing into a signature.

### Proof of Concept
1. Miner M1 (tenure `T1`, pubkey `P1`) proposes block `B` at height `h`; signer's `current_miner = ActiveMiner{T1, P1, ...}`; `check_proposal` passes; node validates OK; signer pre-commits (`mark_pre_committed`), per `stacks-signer/src/v0/signer.rs` handling described in `docs/signer-flows.md` section 4.
2. A new sortition elects miner M2 (tenure `T2`, pubkey `P2`); enough peers' `StateMachineUpdate`s (or the local node's own tip) cause `capitulate_viewpoint`→`capitulate_miner_view` to switch the signer's `current_miner` to `ActiveMiner{T2, P2, ...}` (`stacks-signer/src/v0/signer_state.rs:888-977`), with no block yet signed in `T2`.
3. Enough peers who pre-committed before step 2 push the pre-commit weight for `B` over 70%; `handle_block_pre_commit` re-runs `check_block_against_signer_db_state` (tenure-continuity) and the conflict search (`get_signed_conflicts`/`reorg_permit_stands`), both of which pass because there's no recorded conflicting *signed* block in `T2` yet and `B` is still internally consistent with `T1`.
4. The signer signs `B`, producing a signature over a block tied to a tenure/miner (`T1`/`P1`) that no longer matches its own `current_miner` view (`T2`/`P2`) — the exact class of "sign despite parameters having changed underneath the check" from the source report.

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L118-163)
```rust
    ) -> Result<(), RejectReason> {
        let MinerState::ActiveMiner {
            current_miner_pkh,
            tenure_id,
            parent_tenure_id,
            ..
        } = &self.signer_state.current_miner
        else {
            info!(
                "No valid current miner. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash()
            );
            return Err(RejectReason::InvalidMiner);
        };
        if &block.header.consensus_hash != tenure_id {
            info!("Miner block proposal consensus hash does not match the current miner's tenure id. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "block_consensus_hash" => %block.header.consensus_hash,
                "active_miner_tenure_id" => %tenure_id,
                "active_miner_parent_tenure_id" => %parent_tenure_id,
            );
            return Err(RejectReason::ConsensusHashMismatch {
                actual: block.header.consensus_hash.clone(),
                expected: tenure_id.clone(),
            });
        }
        let Some(miner_pk) = block.header.recover_miner_pk() else {
            warn!("Failed to recover miner pubkey";
                  "signer_signature_hash" => %block.header.signer_signature_hash(),
                  "consensus_hash" => %block.header.consensus_hash);
            return Err(RejectReason::IrrecoverablePubkeyHash);
        };
        let miner_pkh = Hash160::from_data(&miner_pk.to_bytes_compressed());
        if current_miner_pkh != &miner_pkh {
            warn!(
                "Miner block proposal pubkey does not match the winning pubkey hash for its sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "proposed_block_pubkey" => &miner_pk.to_hex(),
                "proposed_block_pubkey_hash" => %miner_pkh,
                "active_miner_pubkey_hash" => %current_miner_pkh,
            );
            return Err(RejectReason::PubkeyHashMismatch);
        }
```

**File:** stacks-signer/src/v0/signer_state.rs (L888-937)
```rust
    /// Updates the local state machine's viewpoint as necessary based on the global state
    #[allow(clippy::too_many_arguments)]
    pub fn capitulate_viewpoint(
        &mut self,
        stacks_client: &StacksClient,
        signerdb: &mut SignerDb,
        eval: &mut GlobalStateEvaluator,
        local_supported_signer_protocol_version: u64,
        sortition_state: &mut Option<SortitionsView>,
        capitulate_miner_view_timeout: Duration,
        tenure_last_block_proposal_timeout: Duration,
        last_capitulate_miner_view: &mut SystemTime,
    ) {
        // We should do this without waiting for capitulation checks, as protocol version updates are orthogonal to capitulation
        self.update_protocol_version(stacks_client, eval, local_supported_signer_protocol_version);

        if !self.is_capitulation_check_ready(
            signerdb,
            local_supported_signer_protocol_version,
            capitulate_miner_view_timeout,
            last_capitulate_miner_view,
        ) {
            return;
        }
        *last_capitulate_miner_view = SystemTime::now();
        // First, update our parent tenure last block if needed. We may have timed out our view of it.
        // This is a bit of an expensive call (due to call for node tip) so we don't want to do it if
        // the node is advancing with our participation.
        self.update_parent_tenure_last_block(
            stacks_client,
            signerdb,
            local_supported_signer_protocol_version,
            tenure_last_block_proposal_timeout,
        );
        let Ok(local_update) =
            self.try_into_update_message_with_version(local_supported_signer_protocol_version)
        else {
            return;
        };

        // Is there a miner view to which we should capitulate?
        let Some(new_miner) = self.capitulate_miner_view(
            stacks_client,
            eval,
            signerdb,
            &local_update,
            tenure_last_block_proposal_timeout,
        ) else {
            return;
        };
```

**File:** stacks-signer/src/v0/signer_state.rs (L943-977)
```rust
        if current_miner != &new_miner {
            info!("Signer State: Capitulating local state machine's current miner viewpoint";
                "current_miner" => ?current_miner,
                "new_miner" => ?new_miner,
                "burn_block" => %burn_block,
                "burn_block_height" => burn_block_height,
                "tx_replay_set" => ?tx_replay_set,
            );
            crate::monitoring::actions::increment_signer_agreement_state_change_reason(
                crate::monitoring::SignerAgreementStateChangeReason::MinerViewUpdate,
            );
            Self::monitor_miner_parent_tenure_update(current_miner, &new_miner);

            *self = Self::Initialized(SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: new_miner.clone().into(),
                active_signer_protocol_version: local_update.active_signer_protocol_version,
                tx_replay_set,
            });

            match new_miner {
                StateMachineUpdateMinerState::ActiveMiner {
                    current_miner_pkh, ..
                } => {
                    if let Some(sortition_state) = sortition_state {
                        // if there is a mismatch between the new_miner ad the current sortition view, mark the current miner as invalid
                        if current_miner_pkh != sortition_state.cur_sortition.data.miner_pkh {
                            sortition_state.cur_sortition.miner_status =
                                SortitionMinerStatus::InvalidatedBeforeFirstBlock
                        }
                    }
                }
                StateMachineUpdateMinerState::NoValidMiner => (),
            }
```

**File:** docs/signer-flows.md (L244-268)
```markdown
    ALREADY -- no --> VALID{"validated ok?<br/>valid = true"}
    VALID -- no --> N2(["wait for validation"])
    VALID -- yes --> TH{"pre-commit weight ≥ 70%?<br/>NakamotoBlockHeader::<br/>compute_voting_weight_threshold"}
    TH -- no --> N3(["wait for more pre-commits"])
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> CONF["signed conflicts at height ≥ h,<br/>in ANY tenure<br/>get_signed_conflicts"]
    CONF --> PERM{"covered by a reorg permit whose<br/>permitting sortition is still canonical?<br/>reorg_permit_stands"}
    PERM -- yes --> EXCL(["excluded — our signature must not<br/>block a replacement we sanctioned"]):::good
    PERM -- no --> FRESH{"any of them still fresh?<br/>last_endorsed > cutoff"}
    FRESH -- yes --> SORT{"conflict_still_blocks, question 1:<br/>is its tenure's sortition still on the<br/>canonical burn chain?<br/>get_sortition_by_burn_hash"}
    SORT -- "404, with the node's burnchain tip<br/>at or past the burn block — a fork<br/>orphaned the tenure" --> OWN
    SORT -- "canonical, or we never<br/>saved its burn block" --> LIVE{"question 2: does the node's chain<br/>still reach the block itself?<br/>get_tenure_tip(its tenure)"}
    SORT -- "could not ask, or 404 with the<br/>node's tip still below the burn block" --> HOLD1
    LIVE -- "yes — real chain state" --> HOLD1["refuse to sign for now<br/>(may sign once conflict is stale)"]:::hold
    LIVE -- "no, and it was<br/>globally accepted" --> OWN
    LIVE -- "no, only locally accepted<br/>— but above this height" --> OWN
    LIVE -- "no, only locally accepted<br/>and a sibling at this height" --> HOLD1
    LIVE -- "could not ask" --> HOLD1
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
```

**File:** docs/signer-flows.md (L421-437)
```markdown
`check_block_against_signer_db_state` returns `SortitionViewMismatch`, or
`ConnectivityIssues` when the lookup itself errored rather than answering; the v2
`check_proposal` path returns `InvalidParentBlock`.

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
