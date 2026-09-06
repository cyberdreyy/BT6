### Title
Nondeterministic tie-break in `capitulate_miner_view` can make signers converge on different, conflicting miner views - ([File: libsigner/src/v0/signer_state.rs])

### Summary
`SignerStateMachine::capitulate_miner_view` (invoked from `capitulate_viewpoint`) selects the "winning" miner state a signer should capitulate to by collecting candidate `StateMachineUpdateMinerState::ActiveMiner` entries into a `HashMap`/`HashSet`, then sorting the resulting `(block_height, miner_state)` pairs and taking `.last()`. When two distinct candidate miner states tie on `block_height` (the burn height of `tenure_id`, resolved via `signerdb.get_burn_block_by_ch(tenure_id)`), the element returned by `.last()` after `sort_by_key` depends on the *pre-sort* relative order of the tied elements, which in turn depends on `HashSet`/`HashMap` iteration order - not deterministic or agreed-upon across signers. This is the same defect class as `sortVaultsByDelta`: a selection routine that is supposed to unambiguously identify one distinguished element (max/min) but, on ties, silently falls back to an arbitrary/implementation-dependent choice instead of asserting uniqueness or defining a canonical tie-break.

### Finding Description
In `capitulate_miner_view` [1](#0-0) , each address's reported `ActiveMiner` state (`tenure_id`, `parent_tenure_id`, `current_miner_pkh`, `parent_tenure_last_block_height`) is used as the `HashMap` key `miners`, and its accumulated weight is checked against `eval.reached_disagreement(*entry)` (≈30% blocking-minority) and, when the tenure already has a globally accepted block (`nmb_blocks > 0`), that 30% bar alone is sufficient to be considered a "potential match" - `eval.reached_agreement` (≈70%) is only required when `nmb_blocks == 0`:

```
let entry = miners.entry(miner_state).or_insert(0);
*entry += weight;
if !eval.reached_disagreement(*entry) { continue; }
...
if nmb_blocks == 0 && !eval.reached_agreement(*entry) { continue; }
```

Qualifying candidates are inserted into a `HashSet<(u64, &StateMachineUpdateMinerState)>` called `potential_matches` [2](#0-1) . The final selection is:

```
let mut potential_matches: Vec<_> = potential_matches.into_iter().collect();
potential_matches.sort_by_key(|(block_height, _)| *block_height);
let new_miner = potential_matches.last().map(|(_, miner)| (*miner).clone());
``` [3](#0-2) 

`sort_by_key` is stable, so when two candidates share the same `block_height`, whichever came first in the `HashSet`-derived `Vec` stays first, and the *other* one is returned by `.last()`. `HashSet`/`HashMap` iteration order in Rust's default `RandomState` hasher is randomized per-process and not agreed between signers (or even between two calls with different insertion histories on the same signer). Since two different `miners` entries with the *same* `tenure_id` (hence the same `block_height` from `get_burn_block_by_ch`) can legitimately arise — e.g. differing only in `current_miner_pkh` or `parent_tenure_last_block_height` reported by different subsets of signers for the same tenure — a tie at the block-height granularity used for tie-breaking is a realistic occurrence, not a theoretical edge case.

This exactly mirrors the `sortVaultsByDelta` root cause: a routine meant to deterministically identify "the" winning candidate instead falls through to an unspecified/implementation-defined choice when the comparison key does not fully discriminate between candidates, and the routine never asserts that the winner is unique.

### Impact Explanation
If a blocking minority (~30% weight, achievable without a majority when the tenure already has ≥1 globally accepted block) manufactures or naturally produces a second `ActiveMiner` candidate that ties in `block_height` with the legitimate ~70%-agreed candidate (or with another minority candidate), different honest signers - each with their own randomized `HashSet` iteration order and their own accumulation order over `address_updates` - can pick different `miner_state` values as "the" state to capitulate to. This breaks the equality/wedge class called out in scope: signers no longer converge on the same "approved-parent vs canonical" miner view. Some signers then sign/pre-commit under one miner_pkh/tenure view while others use a different (possibly non-canonical) one, which can manifest as a subset of the honest signer set validating and signing blocks for the "wrong" miner relative to the rest of the network, or the network wedging (splitting weight across two rival `current_miner` states so that neither `LocalStateMachine` reaches agreement, since votes for the "same" tenure but different `current_miner_pkh` are tracked under different `miners` keys and don't accumulate together).

Given the requirement in the rules that only a blocking minority (not a signer majority) is needed to create the second candidate (because `reached_disagreement` alone, when `nmb_blocks > 0`, is sufficient to enter `potential_matches`), this is reachable without a majority of colluding signers.

### Likelihood Explanation
This requires: (1) a burn view where a tenure already has a globally accepted block (a routine, frequent condition, not attacker-controlled), and (2) at least ~30% signer weight reporting a differing `ActiveMiner` state for the *same* tenure (e.g. divergent `current_miner_pkh` seen from a delayed/duplicated miner rotation, or crafted `StateMachineUpdate` gossip from a blocking-minority set of malicious/faulty signers). The tie-break failure itself is deterministic given such a tie exists — the ambiguity comes purely from `HashSet` iteration order, so it will manifest whenever the tie condition occurs; it does not require a majority, matching the scope's bar for reachable analogs. However, engineering an exact tie (`block_height` collision) intentionally still requires some coordination or a fortuitous timing race, so likelihood is judged medium rather than trivial.

### Recommendation
Replace the reliance on `HashSet`/`HashMap` iteration order with a fully deterministic tie-break:
1. Collect potential matches into a `Vec` (not `HashSet`) and sort by a total order: `(block_height, weight, miner_state-derived deterministic key)` instead of `block_height` alone.
2. If, after sorting, more than one candidate shares the maximal key, either (a) refuse to capitulate (return `None`, analogous to `require(maxIndex != minIndex)`), or (b) apply an explicit deterministic tie-breaker (e.g., largest accumulated weight, then lexicographically smallest `tenure_id`/`current_miner_pkh`) that is identical across all signers.
3. Audit `determine_global_burn_view`, `determine_global_state`, and `determine_latest_supported_signer_protocol_version` in `libsigner/src/v0/signer_state.rs`, which use the same `HashMap`-accumulate-then-return-first-to-cross-threshold pattern, for the same class of order-dependence, even though those specifically return on first-threshold-crossing rather than sorting - confirm iteration order cannot influence *which* value is returned when two entries could plausibly cross the threshold in the same evaluation.

### Proof of Concept
Conceptual sequence (concrete PoC would require running two signer processes so their `HashSet` iteration orders differ, which is why this is presented as a reachable-code-path argument rather than a byte-level exploit trace):
1. Tenure `T` has a globally accepted block, so `nmb_blocks > 0` for `T`.
2. Signer weight is split: ~35% report `ActiveMiner{tenure_id: T, current_miner_pkh: A, ...}`, ~35% report `ActiveMiner{tenure_id: T, current_miner_pkh: B, ...}`, remaining weight is on other tenures/burn views entirely absent from this comparison.
3. Both `A`- and `B`-keyed entries in `miners` cross `reached_disagreement` (30%) and, since `nmb_blocks>0`, bypass the `reached_agreement` (70%) requirement, so both are inserted into `potential_matches` with the *same* `block_height` (both derive from `get_burn_block_by_ch(T)`).
4. On Signer 1, `HashSet` iteration order places `(height, A)` before `(height, B)`; `sort_by_key` (stable) keeps that order; `.last()` returns `B`.
5. On Signer 2 (different process, different `RandomState` seed), iteration order places `(height, B)` before `(height, A)`; `.last()` returns `A`.
6. Signer 1 and Signer 2 now capitulate their `LocalStateMachine::current_miner` to different `ActiveMiner` states for the same tenure, per `capitulate_viewpoint` [4](#0-3) , causing divergent downstream signing/validation decisions between honest signers for the same tenure.

### Citations

**File:** stacks-signer/src/v0/signer_state.rs (L928-962)
```rust
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

        let (burn_block, burn_block_height) = local_update.content.burn_block_view();
        let current_miner = local_update.content.current_miner();
        let tx_replay_set = local_update.content.tx_replay_set();

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
```

**File:** stacks-signer/src/v0/signer_state.rs (L1019-1054)
```rust
        let mut miners = HashMap::new();
        let mut potential_matches = HashSet::new();

        for (address, update) in &eval.address_updates {
            let Some(weight) = eval.address_weights.get(address) else {
                continue;
            };
            let burn_block = update.content.burn_block_view().0;
            if burn_block != global_burn_block {
                continue;
            }
            let miner_state = update.content.current_miner();
            let StateMachineUpdateMinerState::ActiveMiner {
                tenure_id,
                parent_tenure_last_block_height,
                parent_tenure_id,
                ..
            } = miner_state
            else {
                // Only consider potential active miners
                continue;
            };

            let entry = miners.entry(miner_state).or_insert(0);
            *entry += weight;
            if !eval.reached_disagreement(*entry) {
                // We don't even see a blocking minority threshold. Ignore.
                continue;
            }

            let nmb_blocks = signerdb
                .get_globally_accepted_block_count_in_tenure(tenure_id)
                .unwrap_or(0);
            if nmb_blocks == 0 && !eval.reached_agreement(*entry) {
                continue;
            }
```

**File:** stacks-signer/src/v0/signer_state.rs (L1056-1090)
```rust
            match signerdb.get_burn_block_by_ch(tenure_id) {
                Ok(block) => {
                    // Don't query the node or signer db every time if we don't have to...
                    let potential_match = (block.block_height, miner_state);
                    if potential_matches.contains(&potential_match) {
                        continue;
                    };
                    let Ok((local_parent_tenure_last_block_height, _)) =
                        Self::get_parent_tenure_last_block(
                            stacks_client,
                            signerdb,
                            tenure_last_block_proposal_timeout,
                            parent_tenure_id,
                        )
                        .inspect_err(|e| {
                            warn!(
                                "Signer State: Failed to fetch last block in parent tenure";
                                "parent_tenure_id" => %parent_tenure_id,
                                "err" => ?e,
                            )
                        })
                    else {
                        continue;
                    };
                    if local_parent_tenure_last_block_height < *parent_tenure_last_block_height {
                        // We haven't processed this stacks block yet.
                        debug!(
                            "Signer State: A threshold number of signers have a longer active miner parent tenure view. Signer may have an oudated view.";
                            "parent_tenure_id" => %parent_tenure_id,
                            "local_parent_tenure_last_block_height" => local_parent_tenure_last_block_height,
                            "parent_tenure_last_block_height" => parent_tenure_last_block_height,
                        );
                        continue;
                    }
                    potential_matches.insert(potential_match);
```

**File:** stacks-signer/src/v0/signer_state.rs (L1105-1114)
```rust
        let mut potential_matches: Vec<_> = potential_matches.into_iter().collect();
        potential_matches.sort_by_key(|(block_height, _)| *block_height);

        let new_miner = potential_matches.last().map(|(_, miner)| (*miner).clone());
        if new_miner.is_none() {
            crate::monitoring::actions::increment_signer_agreement_state_conflict(
                crate::monitoring::SignerAgreementStateConflict::MinerView,
            );
        }
        new_miner
```
