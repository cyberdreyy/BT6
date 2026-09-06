No vulnerability found for this question.

**Analysis supporting this conclusion:**

The premise assumes the correct behavior is to re-derive `total_weight`/`weight_threshold` per current burn height as it crosses a cycle boundary mid-tenure. That premise is incorrect. The protocol's actual rule — enforced identically on both the miner/signer-coordination side and the authoritative node-validation side — is that the reward set for a tenure is fixed at the tenure's *election* sortition, not re-derived as burn height advances within the tenure.

- `StackerDBListener::new` derives `total_weight`/`weight_threshold` from the `reward_set` and `election_block` passed in at `SignerCoordinator::new` construction time, which is the tenure's election snapshot, not the "current" tip. [1](#0-0) 

- `SignerCoordinator::propose_block` derives the `reward_cycle` field sent in every `BlockProposal` from `election_sortition.block_height` (the fixed tenure election point), not from the current burn tip, for every proposal made across the tenure's lifetime. [2](#0-1) 

- Individual signers reject any `BlockProposal` whose `reward_cycle` doesn't match their own configured `self.reward_cycle`, so a miner cannot get a block signed under the "wrong" reward-cycle view even if it tried. [3](#0-2) 

- Critically, node-side finalization in `NakamotoChainState::accept_block` independently re-verifies signatures via `verify_signer_signatures(reward_set, epoch_id)`, using the **same tenure-election-based reward set** — this is the authoritative safety check, wholly independent of whatever cached `weight_threshold` the miner's `SignerCoordinator` used. [4](#0-3) 

- `load_nakamoto_reward_set_for_tenure` documents this exact design decision explicitly: "a tenure extended across a reward-cycle boundary is still signed by the reward set that was active at its election." [5](#0-4) 

- Full block processing (`process_next_nakamoto_block`) also loads the reward set based on `elected_in_cycle` (the cycle at the tenure's election height), not the block's own coinbase/current height, confirming this is a chain-wide invariant, not merely a signer-coordinator convenience. [6](#0-5) 

Because the miner-side `weight_threshold`, the signer-side `reward_cycle` gate, and the node-side `verify_signer_signatures` reward set are all consistently pinned to the tenure's *election* point — not the current/advancing burn height — there is no divergence to exploit. A late block pushing burn height into cycle N+1 mid-tenure does not create a stale-vs-fresh threshold mismatch; it is the intended, uniformly-applied rule across all three components. An unprivileged single-slot attacker gains nothing from this "boundary-straddling" scenario because the authoritative safety check (`accept_block`/`verify_signer_signatures`) enforces the correct weight/threshold pairing from the same reward set regardless of what the miner's local `SignerCoordinator` cached.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L208-219)
```rust
        let total_weight = reward_set.total_signing_weight().map_err(|e| {
            warn!("Failed to calculate total weight for the reward set: {e:?}");
            ChainstateError::NoRegisteredSigners(0)
        })?;

        let weight_threshold = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)?;

        let reward_cycle_id = burnchain
            .block_height_to_reward_cycle(burn_tip.block_height)
            .expect("FATAL: tried to initialize coordinator before first burn block height");
        let signer_set =
            u32::try_from(reward_cycle_id % 2).expect("FATAL: reward cycle id % 2 exceeds u32");
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L294-303)
```rust
        let reward_cycle_id = burnchain
            .block_height_to_reward_cycle(election_sortition.block_height)
            .expect("FATAL: tried to initialize coordinator before first burn block height");

        let block_proposal = BlockProposal {
            block: block.clone(),
            burn_height: election_sortition.block_height,
            reward_cycle: reward_cycle_id,
            block_proposal_data: BlockProposalData::from_current_version(miner_diagnostic_data),
        };
```

**File:** stacks-signer/src/v0/signer.rs (L1582-1589)
```rust
        if block_proposal.reward_cycle != self.reward_cycle {
            // We are not signing for this reward cycle. Ignore the block.
            debug!(
                "{self}: Received a block proposal for a different reward cycle. Ignore it.";
                "requested_reward_cycle" => block_proposal.reward_cycle
            );
            return;
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2327-2344)
```rust
        let elected_height = sort_db
            .get_consensus_hash_height(&next_ready_block.header.consensus_hash)?
            .ok_or_else(|| ChainstateError::NoSuchBlockError)?;
        let elected_in_cycle = sort_db
            .pox_constants
            .block_height_to_reward_cycle(sort_db.first_block_height, elected_height)
            .ok_or_else(|| {
                ChainstateError::InvalidStacksBlock(
                    "Elected in block height before first_block_height".into(),
                )
            })?;
        let active_reward_set = OnChainRewardSetProvider::<DummyEventDispatcher>(None).read_reward_set_nakamoto_of_cycle(
            elected_in_cycle,
            stacks_chain_state,
            sort_db,
            &next_ready_block.header.parent_block_id,
            true,
        ).map_err(|e| {
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2917-2925)
```rust
        let signing_weight = block
            .header
            .verify_signer_signatures(reward_set, epoch_id)
            .inspect_err(|e| {
                warn!("Received block, but the signer signatures are invalid";
                    "block_id" => %block_id,
                    "error" => ?e,
                );
            })?;
```

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L359-365)
```rust
/// Load the reward set that was active when a Nakamoto tenure was elected.
///
/// `tenure_snapshot` must be the snapshot of the sortition that elected the tenure (the
/// sortition whose consensus hash the tenure's blocks carry), not the burnchain tip: a tenure
/// extended across a reward-cycle boundary is still signed by the reward set that was active
/// at its election. Load errors are folded into `ChainstateError` as block acceptance has
/// historically classified them.
```
