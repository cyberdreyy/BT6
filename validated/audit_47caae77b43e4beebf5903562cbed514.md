## Analysis: `GlobalStateEvaluator::determine_global_state` — mismatched aggregate sets combined into one state machine

### Title
Global signer state combines independently-tallied burn-view/miner agreement and tx-replay-set agreement from potentially disjoint signer subsets - ([File: libsigner/src/v0/signer_state.rs])

### Summary
`GlobalStateEvaluator::determine_global_state` computes two *separate* weighted tallies in a single pass over `address_updates`: one keyed by `(burn_block, burn_block_height, current_miner, active_signer_protocol_version)` (`state_views`), and one keyed purely by `tx_replay_set` (`tx_replay_sets`). Each tally independently reaches "≥70% weight" agreement, and whichever one crosses the threshold first is latched (`found_state_view` / `found_replay_set`). The final `SignerStateMachine` is assembled by splicing the winning `tx_replay_set` onto the winning `state_view`, without ever verifying that the same set of signers (or even an overlapping majority) voted for both components together. [1](#0-0) 

### Finding Description
The loop iterates the map once, accumulating weight into `state_views` (keyed on burn view + miner + protocol version, with `tx_replay_set` zeroed out) and into `tx_replay_sets` (keyed only on the replay set), from the *same* per-address updates but treating the two dimensions as independent axes: [2](#0-1) 

Because a `StateMachineUpdate` bundles both a miner/burn view and a `tx_replay_set` as one atomic vote from a given signer, correctness requires that the *same* supermajority endorsed both facts jointly. Instead, this code effectively behaves like the Directus bug: two different "policies" (here, two different weighted majorities) are evaluated independently, and the union/composition of their conclusions is presented as if it were one item's authorized state, without checking that the item (i.e., the miner/burn-view being enacted) is the one the replay-set-agreeing majority actually attached that replay set to.

Concretely: suppose signers A,B,C (70% weight) agree on `(burn_view=X, miner=M1)` but have replay_set=`[]`, while signers D,E (30%) have a different burn view `(burn_view=Y, miner=M2)` but all signers happen to also converge (through unrelated timing) on some non-trivial `tx_replay_set=[TX1]` at >70% weight (e.g., because two disjoint subsets, one from each burn-view group, coincidentally report the same replay-set value, or because `find_majority_prefix_replay_set`'s LCP composition draws weight from signers who are not part of the state-view majority at all). `found_replay_set` is latched independently of which signers contributed to `found_state_view`. The final state machine then reports `(burn_view=X, miner=M1, tx_replay_set=[TX1])` — a combination that no single supermajority ever actually voted for jointly. Since `determine_global_state()` return value gates `check_block_against_global_state` (`GlobalStateView::check_proposal`) used in `handle_block_proposal`/`check_block_against_signer_db_state`, this can cause a signer to accept/reject block proposals based on a replay-set/miner combination that doesn't correspond to any real quorum's intent — i.e., a fabricated "global state" that the protocol equality (aggregated-weight vs. verified-joint-accepts) is supposed to prevent. [3](#0-2) 

The `find_majority_prefix_replay_set` fallback compounds this: it computes an LCP across all reported replay sets and sums weight for every signer whose set merely `starts_with` the candidate prefix, again without regard to which burn-view/miner state that signer voted for: [4](#0-3) 

### Impact Explanation
This can cause the signer's notion of "global agreed state" (`SignerStateMachine`, including which miner is authorized and what tx-replay-set constraint applies) to be a Frankenstein composite that no real supermajority endorsed as a unit. Since this state gates block-proposal validation (`check_block_against_global_state`) and drives `tx_replay_scope`/replay-transaction handling, a wedge or mis-acceptance is possible: a signer could apply a replay-transaction-set constraint that doesn't correspond to the miner it believes is active, or vice versa, leading to either spurious rejections (liveness wedge - signer never validates blocks it should) or, in the worst case, validating/signing a block under an inconsistent state view that doesn't reflect any real 70% consensus. This maps to the "High" tier: a signer potentially acting on a stale/inconsistent view of the reward set's agreed state.

### Likelihood Explanation
This requires no majority collusion by an attacker — it can occur under entirely honest but transiently divergent signer views (a normal fork/timing race across `StateMachineUpdate` gossip, which any one-slot miner or normal network jitter can induce), since `address_updates` is populated purely from gossiped `StateMachineUpdate` messages that signers naturally disagree on during transitions. The two tallies are computed off the same natural data without any correlation check, so this is a structural (not attacker-privileged) inconsistency window. However, whether it's reliably *exploitable* to force a wedge or an invalid acceptance (rather than just transient inconsistency that self-heals as more updates arrive) is uncertain from static analysis alone — reproducing the precise mis-composition requires timing multiple `StateMachineUpdate`s from a live signer set, which I could not fully verify by static reading of `libsigner/src/v0/signer_state.rs` and `libsigner/src/tests/signer_state.rs` alone.

### Recommendation
Change `determine_global_state` to tally `(state_view, tx_replay_set)` jointly per signer (i.e., only allow the replay set to be attributed to a state view if it comes from the weight that agreed on that specific state view), or require that `found_replay_set`'s contributing weight be a subset of `found_state_view`'s contributing signers before splicing them together. At minimum, add a check that the signers whose weight satisfied `reached_agreement` for the replay set overlap sufficiently (i.e. by weight) with the signers whose weight satisfied agreement for the state view, similar to how the Directus fix moved from "union of separately-permitted fields" to "per-item verification of the specific fields requested."

### Proof of Concept
Could not fully construct a live signer-set PoC within static analysis; the mechanism is demonstrated structurally by the code in `determine_global_state`, and existing unit tests in [5](#0-4)  exercise the two tallies as independently-satisfiable dimensions but do not test the case where the replay-set-agreeing weight and the state-view-agreeing weight come from disjoint/non-overlapping signer subsets, which is the scenario that would prove the exploit concretely. A background Devin session with the ability to run the signer test harness would be needed to construct a concrete reproduction (e.g., extending `determine_global_states_with_tx_replay_set` with disjoint signer groups for the two axes) and confirm whether `check_block_against_global_state` acts on the resulting inconsistent composite state in a way that breaks a signing/rejection guarantee.

### Citations

**File:** libsigner/src/v0/signer_state.rs (L102-157)
```rust
    pub fn determine_global_state(&self) -> Option<SignerStateMachine> {
        let active_signer_protocol_version =
            self.determine_latest_supported_signer_protocol_version()?;
        let mut state_views = HashMap::new();
        let mut tx_replay_sets = HashMap::new();
        let mut found_state_view = None;
        let mut found_replay_set = None;
        for (address, update) in &self.address_updates {
            let Some(weight) = self.address_weights.get(address) else {
                continue;
            };
            let (burn_block, burn_block_height) = update.content.burn_block_view();
            let current_miner = update.content.current_miner();
            let tx_replay_set = update.content.tx_replay_set();

            let state_machine = SignerStateMachine {
                burn_block: burn_block.clone(),
                burn_block_height,
                current_miner: current_miner.clone().into(),
                active_signer_protocol_version,
                // We need to calculate the threshold for the tx_replay_set separately
                tx_replay_set: ReplayTransactionSet::none(),
            };
            let key = SignerStateMachineKey(state_machine.clone());
            let entry = state_views.entry(key).or_insert_with(|| 0);
            *entry += weight;

            if self.reached_agreement(*entry) {
                found_state_view = Some(state_machine);
            }

            let replay_entry = tx_replay_sets
                .entry(tx_replay_set.clone())
                .or_insert_with(|| 0);
            *replay_entry += weight;

            if self.reached_agreement(*replay_entry) {
                found_replay_set = Some(tx_replay_set);
            }
            if found_replay_set.is_some() && found_state_view.is_some() {
                break;
            }
        }
        // Try to find agreed replay set, or find longest common prefix if no exact agreement
        let final_replay_set = if let Some(tx_replay_set) = found_replay_set {
            tx_replay_set
        } else {
            // No exact agreement found, try finding longest common prefix with majority support
            self.find_majority_prefix_replay_set(&tx_replay_sets)
                .unwrap_or_else(ReplayTransactionSet::none)
        };

        if let Some(state_view) = found_state_view.as_mut() {
            state_view.tx_replay_set = final_replay_set;
        }
        found_state_view
```

**File:** libsigner/src/v0/signer_state.rs (L236-251)
```rust
        // Start with the most supported replay set as initial candidate
        if let Some((initial_set, _)) = sorted_sets.first() {
            let mut candidate_prefix = initial_set.0.clone();
            let mut total_supporting_weight = 0u32;

            // Find all sets that support the current candidate prefix
            for (replay_set, weight) in tx_replay_sets {
                if replay_set.0.starts_with(&candidate_prefix) {
                    total_supporting_weight = total_supporting_weight.saturating_add(*weight);
                }
            }

            // If the initial candidate already has majority support, return it
            if self.reached_agreement(total_supporting_weight) {
                return Some(ReplayTransactionSet::new(candidate_prefix));
            }
```

**File:** stacks-signer/src/v0/signer.rs (L944-975)
```rust
    fn check_block_against_global_state(
        &mut self,
        stacks_client: &StacksClient,
        block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = block.header.signer_signature_hash();
        let block_id = block.block_id();
        let Some(global_state) = self.global_state_evaluator.determine_global_state() else {
            warn!(
                "{self}: Cannot validate block, no global signer state";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
                "local_signer_state" => ?self.local_state_machine
            );
            return Some(self.create_block_rejection(RejectReason::NoSignerConsensus, block));
        };

        let global_state_view = GlobalStateView {
            signer_state: global_state,
            config: self.proposal_config.clone(),
        };

        info!(
            "{self}: Evaluating proposal against global state";
            "signer_state" => ?global_state_view.signer_state,
            "signer_signature_hash" => %signer_signature_hash,
            "block_id" => %block_id,
            "local_signer_state" => ?self.local_state_machine,
        );

        // Check if proposal can be rejected now if not valid against the global state
        match global_state_view.check_proposal(stacks_client, &mut self.signer_db, block) {
```

**File:** libsigner/src/tests/signer_state.rs (L399-531)
```rust
#[test]
fn determine_global_states_with_tx_replay_set() {
    let mut global_eval = generate_global_state_evaluator(5);

    let addresses: Vec<_> = global_eval.address_weights.keys().cloned().collect();
    let local_address = addresses[0].clone();
    let local_update = global_eval
        .address_updates
        .get(&local_address)
        .unwrap()
        .clone();
    let StateMachineUpdateMessage {
        content:
            StateMachineUpdateContent::V0 {
                burn_block,
                burn_block_height,
                current_miner,
            },
        ..
    } = local_update.clone()
    else {
        panic!("Unexpected state machine update message version");
    };

    let local_supported_signer_protocol_version = 1;
    let active_signer_protocol_version = 1;

    let state_machine = SignerStateMachine {
        burn_block,
        burn_block_height,
        current_miner: current_miner.clone().into(),
        active_signer_protocol_version, // a majority of signers are saying they support version the same local_supported_signer_protocol_version, so update it here...
        tx_replay_set: ReplayTransactionSet::none(),
    };

    let burn_block = ConsensusHash([20u8; 20]);
    let burn_block_height = burn_block_height + 1;
    assert_eq!(global_eval.determine_global_state().unwrap(), state_machine);

    let no_tx_replay_set_update = StateMachineUpdateMessage::new(
        active_signer_protocol_version,
        local_supported_signer_protocol_version,
        StateMachineUpdateContent::V1 {
            burn_block: ConsensusHash([20u8; 20]),
            burn_block_height,
            current_miner: current_miner.clone(),
            replay_transactions: vec![],
        },
    )
    .unwrap();

    // Let's update 3 signers to some new tx_replay_set but one that has no txs in it
    for address in addresses.iter().skip(1).take(3) {
        global_eval.insert_update(address.clone(), no_tx_replay_set_update.clone());
    }

    // we have disagreement about the burn block height
    assert!(
        global_eval.determine_global_state().is_none(),
        "We should have disagreement about the burn view"
    );

    global_eval.insert_update(local_address.clone(), no_tx_replay_set_update.clone());

    let new_burn_view_state_machine = SignerStateMachine {
        burn_block: burn_block.clone(),
        burn_block_height,
        current_miner: current_miner.clone().into(),
        active_signer_protocol_version: local_supported_signer_protocol_version, // a majority of signers are saying they support version the same local_supported_signer_protocol_version, so update it here...
        tx_replay_set: ReplayTransactionSet::none(),
    };

    // Let's tip the scales over to the correct burn view
    global_eval.insert_update(local_address.clone(), no_tx_replay_set_update);
    assert_eq!(
        global_eval.determine_global_state().unwrap(),
        new_burn_view_state_machine
    );

    let pk = StacksPrivateKey::random();
    let tx = StacksTransaction {
        version: TransactionVersion::Testnet,
        chain_id: 0x80000000,
        auth: TransactionAuth::from_p2pkh(&pk).unwrap(),
        anchor_mode: TransactionAnchorMode::Any,
        post_condition_mode: TransactionPostConditionMode::Allow,
        post_conditions: vec![],
        payload: TransactionPayload::TokenTransfer(
            local_address.clone().into(),
            123,
            TokenTransferMemo([0u8; 34]),
        ),
    };

    let tx_replay_set_update = StateMachineUpdateMessage::new(
        active_signer_protocol_version,
        local_supported_signer_protocol_version,
        StateMachineUpdateContent::V1 {
            burn_block: burn_block.clone(),
            burn_block_height,
            current_miner: current_miner.clone(),
            replay_transactions: vec![tx.clone()],
        },
    )
    .unwrap();

    // Let's update 3 signers to some new non empty replay set
    for address in addresses.into_iter().skip(1).take(3) {
        global_eval.insert_update(address, tx_replay_set_update.clone());
    }

    // We still have a valid view but with no global tx set so we aren't blocked entirely but also aren't enforcing the tx replays set
    assert_eq!(
        global_eval.determine_global_state().unwrap(),
        new_burn_view_state_machine
    );

    // Let's tip the scales over to require a tx replay set
    global_eval.insert_update(local_address, tx_replay_set_update.clone());

    let tx_replay_state_machine = SignerStateMachine {
        burn_block,
        burn_block_height,
        current_miner: current_miner.into(),
        active_signer_protocol_version,
        tx_replay_set: ReplayTransactionSet::new(vec![tx]),
    };

    assert_eq!(
        global_eval.determine_global_state().unwrap(),
        tx_replay_state_machine
    );
}
```
