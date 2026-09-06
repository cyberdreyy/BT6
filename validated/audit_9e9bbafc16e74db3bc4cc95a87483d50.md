### Title
Miner-forged `reward_cycle` field in `BlockProposal` lets a rogue miner make the correct signer set silently ignore a valid block proposal (liveness wedge) - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`BlockProposal.reward_cycle`, `burn_height`, and `block` are three independent fields set by whoever posts the message to the `.miners` StackerDB slot [1](#0-0) . The signer's dispatch logic uses the self-reported `reward_cycle` field, not a value derived from the block's own consensus data, to decide whether to process a proposal at all:

```rust
if block_proposal.reward_cycle != self.reward_cycle {
    // We are not signing for this reward cycle. Ignore the block.
    ...
    return;
}
``` [2](#0-1) 

This is structurally the same bug class as the AI Arena `reroll(tokenId, fighterType)` finding: a caller-supplied "type/context" tag (`fighterType` there, `reward_cycle` here) is used to select which state/limit/config applies, without validating that the tag actually matches the entity it is attached to (the NFT's real `fighterType`; here, the block's real reward cycle, which is derivable from its tenure/`consensus_hash`/burn height, exactly as `find_nakamoto_block_reward_cycle` does on the node side [3](#0-2) ).

### Finding Description
Nothing in `stacks-signer/src/chainstate/v1.rs` or `v2.rs` (`check_proposal`) re-derives or cross-checks `reward_cycle` against the block's `consensus_hash`/`burn_height` — confirmed by the absence of any `reward_cycle` reference in either file. The only two places that ever look at the field are:

1. `handle_block_proposal`'s gate, shown above, which is the *sole* admission check for whether this signer instance processes the proposal at all [4](#0-3) .
2. Storage of the (unverified) value into `BlockInfo.reward_cycle`, which is later used for the anti-cross-cycle-processing check `block_lookup_by_reward_cycle` [5](#0-4)  — a mechanism that the project's own CHANGELOG says was hardened specifically because messages "that do not apply to blocks from their cycle" were being processed [6](#0-5) . That fix addressed validation *responses*, not the original `BlockProposal.reward_cycle` field, which remains trusted as-is at proposal ingestion.

On the node side, `/v3/block_proposal` validation (`NakamotoBlockProposal::validate`) never even looks at a reward-cycle field — it only checks `chain_id`/`mainnet` and derives everything else (tenure, sortition, burn view) from the block's own header data [7](#0-6) . Final signature verification for propagation likewise re-derives the true reward cycle from the block's sortition/consensus data (`check_nakamoto_block_signer_signature`) rather than trusting any self-reported field [8](#0-7) . So the `reward_cycle` field inside `BlockProposal` exists purely for the signer's own bookkeeping/routing and is never reconciled with the ground truth.

**Break produced:** a legitimate block, correctly built for reward cycle `N` (the currently-active signer set), can be broadcast by the miner with `reward_cycle` forged to `N±1`. Every signer instance running for cycle `N` (the set that is actually supposed to validate and sign it) will hit the `!=` check and drop the message unconditionally — no validation submission, no rejection broadcast, nothing. Only the signer instances that happen to be running for the (wrong) forged cycle would attempt to process it, and their `SortitionsView`/state machine is bound to a different reward-cycle context, so `check_proposal` will legitimately reject it (wrong miner, wrong tenure view, etc., since v1/v2 `check_proposal` derives things like `current_miner`/`parent_tenure_last_block` from that signer's own reward-cycle state, not the block's). The net effect: the block that should have been signed by the correct signer set is silently dropped by exactly the signers who could have signed it.

### Impact Explanation
This is a **liveness wedge**: a single miner (a "one-slot" actor who is the tenure's block producer and thus permitted to write to the `.miners` StackerDB slot) can stall block signing for its own tenure by mislabeling the `reward_cycle` field on otherwise-valid `BlockProposal` messages. Because the check is a hard, silent `return` with no rejection broadcast, none of the tallying logic in section 6 of the signer flow (`handle_block_response`) is triggered either — the correct signers never even record an opinion, so the 70%/30% threshold logic that would normally converge to a rejection and let the miner retry never engages either. This matches the "High — a signer wedged into never signing valid blocks" category in the rules, since the currently-responsible signer set is made to never evaluate a valid proposal that was in fact addressed to it, until the miner (accidentally or not) sends a proposal with the correct `reward_cycle` value.

It does not rise to Critical because it cannot make a signer sign an invalid/non-canonical block, and it cannot forge a valid aggregate/cross-context signature (final on-chain signature verification uses the block's real reward cycle, not the field) — the damage is confined to availability/liveness of the tenure, not to producing an incorrectly-accepted or incorrectly-signed block.

### Likelihood Explanation
Reachable by any single active miner with no cooperation from other signers or majority control, exactly matching the "one-slot miner (plus gossip)" scope of this scan. It requires only setting one integer field differently from what the coordinator would normally compute (the honest coordinator path computes it correctly from `election_sortition.block_height` [9](#0-8) , but nothing on the signer's receiving side enforces that this value is trustworthy). A malicious or buggy miner client can trivially set it to an adjacent cycle number. Likelihood is Medium: it requires a miner willing to sabotage its own tenure (no direct profit motive is obvious, though it could be used to grief a competing tenure/attempt a targeted stall), which is why it is flagged as an analog worth confirming rather than a definite live exploit.

### Recommendation
Do not trust the self-reported `reward_cycle` field for admission control. At `handle_block_proposal`, before comparing to `self.reward_cycle`, derive the block's true reward cycle from consensus data available to the signer (e.g., ask the node for the sortition/tenure of `block_proposal.block.header.consensus_hash`, or use `burn_height` together with the burnchain's own `block_height_to_reward_cycle`, verifying `burn_height` itself is consistent with the sortition the signer already trusts) and reject/ignore based on that derived value, only using the message's own field as a hint. At minimum, log/flag a mismatch between the self-reported `reward_cycle`/`burn_height` and the value the signer independently derives from `election_sortition`/sortition lookups, and prefer the derived value for the routing decision so a forged field cannot suppress processing by the rightful signer set.

### Proof of Concept
1. Boot a `SignerTest` as in `block_proposal_rejection`/`incoming_signers_ignore_block_proposals` with signers active for reward cycle `N` [10](#0-9) .
2. Have the "miner" construct a fully valid `NakamotoBlock` for cycle `N` (correct `consensus_hash`, correct `chain_length`, correctly miner-signed), matching what `mine_and_verify_confirmed_naka_block` builds [11](#0-10) .
3. Wrap it in a `SignerMessageV0::BlockProposal(BlockProposal { block, burn_height: <correct>, reward_cycle: N+1 /* forged */, .. })` (mirroring the struct in [1](#0-0) ) and push it directly onto the `.miners` StackerDB slot instead of going through `SignerCoordinator::propose_block`'s honest computation [12](#0-11) .
4. Observe that every signer for cycle `N` hits the early `return` in `handle_block_proposal` (`block_proposal.reward_cycle != self.reward_cycle`) [2](#0-1)  and emits no validation request and no `BlockResponse` at all — confirmable via `wait_for_validate_ok_response`/`wait_for_validate_reject_response` timing out, unlike the existing `block_proposal_rejection` test which expects an explicit rejection to be observed [13](#0-12) .
5. Confirm the tenure stalls (no `naka_mined_blocks` increment) until a proposal with the correct `reward_cycle` is (re)sent.

Note: I was not able to fully verify from the index whether any StackerDB-slot/contract-level (transport) partitioning independently prevents a mismatched `reward_cycle` payload from ever reaching the "wrong" signer set's stackerdb reader — the docs mention "the opposite-parity set's traffic is never processed" but attribute this to the `SignerMessage`'s own carried reward-cycle/parity data rather than a separate stackerdb-contract-level check [14](#0-13) . If such transport-level parity separation is enforced by contract choice (not by trusting the message field), this reduces to only being exploitable within a signer's own already-addressed slot; a Devin session with access to `stacks-signer/src/client/stackerdb.rs` and the miner-slot contract selection logic would be needed to fully confirm whether the forged field can cross a StackerDB-contract boundary or only mis-routes within one.

### Citations

**File:** libsigner/src/events.rs (L58-69)
```rust
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
/// BlockProposal sent to signers
pub struct BlockProposal {
    /// The block itself
    pub block: NakamotoBlock,
    /// The burn height the block is mined during
    pub burn_height: u64,
    /// The reward cycle the block is mined during
    pub reward_cycle: u64,
    /// Versioned and backwards-compatible block proposal data
    pub block_proposal_data: BlockProposalData,
}
```

**File:** stacks-signer/src/v0/signer.rs (L1574-1589)
```rust
    /// Handle block proposal messages submitted to signers stackerdb
    fn handle_block_proposal(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_proposal: &BlockProposal,
    ) {
        debug!("{self}: Received a block proposal: {block_proposal:?}");
        if block_proposal.reward_cycle != self.reward_cycle {
            // We are not signing for this reward cycle. Ignore the block.
            debug!(
                "{self}: Received a block proposal for a different reward cycle. Ignore it.";
                "requested_reward_cycle" => block_proposal.reward_cycle
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2666-2684)
```rust
    /// Helper for getting the block info from the db while accommodating for reward cycle
    pub fn block_lookup_by_reward_cycle(
        &self,
        block_hash: &Sha512Trunc256Sum,
    ) -> Option<BlockInfo> {
        let block_info = self
            .signer_db
            .block_lookup(block_hash)
            .inspect_err(|e| {
                error!("{self}: Failed to lookup block hash {block_hash} in signer db: {e:?}");
            })
            .ok()
            .flatten()?;
        if block_info.reward_cycle == self.reward_cycle {
            Some(block_info)
        } else {
            None
        }
    }
```

**File:** stackslib/src/net/tests/relay/nakamoto.rs (L536-551)
```rust
                            .network
                            .find_nakamoto_block_reward_cycle(&sortdb, &bad_block);
                        let want = (
                            Some(
                                follower
                                    .network
                                    .burnchain
                                    .block_height_to_reward_cycle(block_sn.block_height)
                                    .unwrap(),
                            ),
                            true,
                        );
                        if got != want {
                            deferred_failures.push(format!(
                                "bad-signature block reward cycle mismatch: {got:?} != {want:?}"
                            ));
```

**File:** stacks-signer/CHANGELOG.md (L241-252)
```markdown

### Changed

- Prevent old reward cycle signers from processing block validation response messages that do not apply to blocks from their cycle.

## [3.1.0.0.2.1]

### Added

### Changed

- Prevent old reward cycle signers from processing block validation response messages that do not apply to blocks from their cycle.
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L556-571)
```rust
        let mainnet = self.chain_id == CHAIN_ID_MAINNET;
        if self.chain_id != chainstate.chain_id || mainnet != chainstate.mainnet {
            warn!(
                "Rejected block proposal";
                "reason" => "Wrong network/chain_id",
                "expected_chain_id" => chainstate.chain_id,
                "expected_mainnet" => chainstate.mainnet,
                "received_chain_id" => self.chain_id,
                "received_mainnet" => mainnet,
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::NetworkChainMismatch,
                reason: "Wrong network/chain_id".into(),
                failed_txid: None,
            });
        }
```

**File:** stackslib/src/net/unsolicited.rs (L247-269)
```rust
    pub(crate) fn check_nakamoto_block_signer_signature(
        &mut self,
        reward_cycle: u64,
        epoch_id: StacksEpochId,
        nakamoto_block: &NakamotoBlock,
    ) -> bool {
        let Some(rc_data) = self.current_reward_sets.get(&reward_cycle) else {
            info!(
                "{:?}: Failed to validate Nakamoto block {}/{}: no reward set for cycle {reward_cycle}",
                self.get_local_peer(),
                &nakamoto_block.header.consensus_hash,
                &nakamoto_block.header.block_hash(),
            );
            return false;
        };
        let Some(reward_set) = rc_data.reward_set() else {
            info!(
                "{:?}: No reward set for reward cycle {}",
                self.get_local_peer(),
                reward_cycle
            );
            return false;
        };
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L294-322)
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

        let block_proposal_message = SignerMessageV0::BlockProposal(block_proposal);

        loop {
            debug!("Sending block proposal message to signers";
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            Self::send_miners_message::<SignerMessageV0>(
                &self.message_key,
                sortdb,
                election_sortition,
                stackerdbs,
                block_proposal_message.clone(),
                MinerSlotID::BlockProposal,
                self.is_mainnet,
                &mut self.miners_session,
                &election_sortition.consensus_hash,
                miner_db,
            )?;
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L798-826)
```rust
    fn mine_and_verify_confirmed_naka_block(
        &self,
        timeout: Duration,
        num_signers: usize,
        use_nakamoto_blocks_mined: bool,
    ) {
        info!("------------------------- Try mining one block -------------------------");

        let reward_cycle = self.get_current_reward_cycle();

        self.mine_nakamoto_block(timeout, use_nakamoto_blocks_mined);
        self.check_signer_states_normal();

        // Verify that the signers accepted the proposed block, sending back a validate ok response
        let proposed_signer_signature_hash = self
            .wait_for_validate_ok_response(timeout.as_secs())
            .signer_signature_hash;
        let message = proposed_signer_signature_hash.0;

        info!("------------------------- Test Block Signed -------------------------");
        // Verify that the signers signed the proposed block
        let signature = self.wait_for_confirmed_block_v0(&proposed_signer_signature_hash, timeout);

        info!("Got {} signatures", signature.len());

        // NOTE: signature.len() does not need to equal signers.len(); the stacks miner can finish the block
        //  whenever it has crossed the threshold.
        assert!(signature.len() >= num_signers * 7 / 10);
        info!("Verifying signatures against signers for reward cycle {reward_cycle:?}");
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L2696-2712)
```rust
    // Propose a block to the signers that passes initial checks but will be rejected by the stacks node
    let view = SortitionsView::fetch_view(proposal_conf, &signer_test.stacks_client).unwrap();
    block.header.pox_treatment = BitVec::ones(1).unwrap();
    block.header.consensus_hash = view.cur_sortition.data.consensus_hash;
    block.header.chain_length = 35; // We have mined 35 blocks so far.
    let block_2_consensus_hash = block.header.consensus_hash.clone();

    block
        .header
        .sign_miner(signer_test.get_miner_key())
        .unwrap();
    let block_signer_signature_hash_2 = block.header.signer_signature_hash();
    signer_test.propose_block(block, short_timeout);

    info!("------------------------- Test Block Proposal Rejected -------------------------");
    // Verify the signers rejected the second block via the endpoint
    let reject = signer_test
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L6077-6146)
```rust
#[test]
#[ignore]
/// Test that signers for an incoming reward cycle, do not sign blocks for the previous reward cycle.
///
/// Test Setup:
/// The test spins up five stacks signers that are stacked for multiple cycles, one miner Nakamoto node, and a corresponding bitcoind.
/// The stacks node is then advanced to Epoch 3.0 boundary to allow block signing.
///
/// Test Execution:
/// The node mines to the middle of the prepare phase of reward cycle N+1.
/// Sends a status request to the signers to ensure both the current and next reward cycle signers are active.
/// A valid Nakamoto block is proposed.
/// Two invalid Nakamoto blocks are proposed.
///
/// Test Assertion:
/// All signers for cycle N sign the valid block.
/// No signers for cycle N+1 emit any messages.
/// All signers for cycle N reject the invalid blocks.
/// No signers for cycle N+1 emit any messages for the invalid blocks.
/// The chain advances to block N.
fn incoming_signers_ignore_block_proposals() {
    if env::var("BITCOIND_TEST") != Ok("1".into()) {
        return;
    }

    tracing_subscriber::registry()
        .with(fmt::layer())
        .with(EnvFilter::from_default_env())
        .init();

    info!("------------------------- Test Setup -------------------------");
    let num_signers = 5;
    let recipient = PrincipalData::from(StacksAddress::burn_address(false));
    let sender_sk = Secp256k1PrivateKey::random();
    let sender_addr = tests::to_addr(&sender_sk);
    let send_amt = 100;
    let send_fee = 180;
    let signer_test: SignerTest<SpawnedSigner> =
        SignerTest::new(num_signers, vec![(sender_addr, send_amt + send_fee)]);
    let timeout = Duration::from_secs(200);
    let http_origin = format!("http://{}", &signer_test.running_nodes.conf.node.rpc_bind);
    signer_test.boot_to_epoch_3();
    let curr_reward_cycle = signer_test.get_current_reward_cycle();
    // Mine to the middle of the prepare phase of the next reward cycle
    let next_reward_cycle = curr_reward_cycle.saturating_add(1);
    let prepare_phase_len = signer_test
        .running_nodes
        .conf
        .get_burnchain()
        .pox_constants
        .prepare_length as u64;
    let middle_of_prepare_phase = signer_test
        .running_nodes
        .btc_regtest_controller
        .get_burnchain()
        .reward_cycle_to_block_height(next_reward_cycle)
        .saturating_sub(prepare_phase_len / 2);

    info!("------------------------- Test Mine Until Middle of Prepare Phase at Block Height {middle_of_prepare_phase} -------------------------");
    signer_test.run_until_burnchain_height_nakamoto(timeout, middle_of_prepare_phase, num_signers);

    signer_test.wait_for_registered_both_reward_cycles();

    let current_burnchain_height = signer_test
        .running_nodes
        .btc_regtest_controller
        .get_headers_height();
    assert_eq!(current_burnchain_height, middle_of_prepare_phase);
    assert_eq!(curr_reward_cycle, signer_test.get_current_reward_cycle());

```

**File:** docs/signer-flows.md (L121-124)
```markdown
own reward cycle has started; everything else is dropped until then. The parity
gate is separate and unconditional: `SignerMessages` carry the signer set they
came from, and the opposite-parity set's traffic is never processed.

```
