I found one genuine analog to the "trust an identity without verifying it before acting" bug class: the epoch 2.5 `mock_sign` path signs a `MockProposal` without ever checking `MockProposal::verify()` against the claimed miner's key.

### Title
Signer mock-signs an epoch-2.5 MockProposal without verifying the miner's signature - ([File: stacks-signer/src/v0/signer.rs])

### Summary
When a signer receives a `SignerMessage::MockProposal` over the miners StackerDB, it checks only the node's epoch and its own reward cycle before calling `mock_sign`, which unconditionally signs the received `MockProposal` and broadcasts a `MockSignature`. `MockProposal::verify()` exists in `libsigner/src/v0/messages.rs` to check the embedded miner signature against a `StacksPublicKey`, but the signer never calls it on the miner-message path. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`handle_event_match` routes `SignerMessage::MockProposal(mock_proposal)` to `mock_sign` after checking only the node epoch (`Epoch25`) and that the signer's own `reward_cycle == current_reward_cycle`; it does not recover/verify the `mock_proposal`'s embedded miner `signature` against the current elected miner's public key before signing it. Contrast this with the equivalent path for `MockBlock` verification done by test-observer code (`stacks-node/src/tests/signer/v0/mod.rs:3854-3862`), which does call `mock_signature.verify(...)` against the expected signer keys — i.e. the codebase's own test helpers demonstrate that this verification is expected and meaningful, yet the production signer path for consuming an incoming `MockProposal` skips it entirely before responding with a `MockSignature`.

Any StackerDB writer able to place a chunk in the `.miners` contract (which is the same coarse permission a "one-slot miner" or any other write-capable participant already has in this threat model) can therefore submit an arbitrary/forged `MockProposal` — with attacker-controlled `PeerInfo` (tip, height, consensus hash, server version) — and get every signer that reaches this code path to produce and broadcast a valid `MockSignature` over it, without that data ever being validated as originating from the legitimate current miner.

### Impact Explanation
This breaks the "signed vs validated" equality for the mock-signing liveness protocol: `MockSignature` is meant to attest "the signer that holds this address observed a genuine miner proposal for the current sortition," but the code lets the signer sign over data whose miner-authorship was never checked. It does not directly forge a consensus block signature (mock signing is out-of-band and does not move `BlockState`), so it does not, by itself, cause a signer to sign an invalid/non-canonical Nakamoto block. Its practical consequence is corrupted liveness telemetry (`multiple_miners_mock_sign_epoch_25`-style monitoring, `SignerMonitor`) and a spoofable proof-of-life signal for epoch-2.5 operation, rather than a chainstate-breaking signature. I flag this explicitly as a lower-confidence/limited-impact finding: it satisfies the CWE-300/639 bug-class analogy (identity trusted without verification, enabling a MITM-equivalent spoof) but I could not establish that it reaches the Critical/High bar defined by the rules (no invalid/non-canonical/conflicting block signature, no rejection miscount, no wedge into never-signing). It is reported for completeness because it is the clearest concrete instance of "verify before trusting/signing" being skipped in the reachable, one-writer-controlled code path, but a reviewer may reasonably judge it out of scope given the required impact bar.

### Likelihood Explanation
The `MockProposal`/`MockSignature` path is only active while the node epoch is `Epoch25`, so it is time-boxed to the Epoch 2.5 window before Nakamoto activation, and requires write access to the `.miners` StackerDB slot pool — the same reachable "one-slot miner" surface referenced in scope. Within that window the trigger is trivial: broadcast one `MockProposal` chunk.

### Recommendation
Before calling `self.mock_sign(mock_proposal.clone())`, verify `mock_proposal.verify(&expected_miner_pubkey)` (recovering/validating against the current elected miner's public key from `SortitionDB`) and drop the message if verification fails or if it doesn't match the currently elected miner.

### Proof of Concept
1. During an Epoch 2.5 reward cycle, construct a `MockProposal { peer_info, signature }` with an arbitrary `signature` (or a signature from an unrelated/attacker-controlled key) and arbitrary `peer_info` fields.
2. Wrap it as `SignerMessage::MockProposal(mock_proposal)` and write it as a chunk to the `.miners` StackerDB contract (any account/slot with write access to that contract, consistent with a "one-slot miner" threat actor).
3. Observe that every signer configured for the current reward cycle whose node epoch is `Epoch25` calls `mock_sign` on the message in `stacks-signer/src/v0/signer.rs:603-621` and broadcasts a `MockSignature` over the forged `peer_info`, with no `MockProposal::verify` call ever having been performed against the legitimate miner's key.

**Caveat:** I was not able to fully trace whether any StackerDB-level or slot-owner check upstream (e.g., in `libsigner`/StackerDB chunk-acceptance) restricts which principal can write to the miner slot such that an arbitrary non-miner could not place this chunk at all; if such a restriction exists and only the legitimate elected miner's key can ever write a `.miners` chunk, this finding would collapse to "no external verification needed because transport already restricts the writer," which would make it out of scope per the exclusion of "transport/StackerDB sync mechanics." I could not confirm this from the index within the given search budget, so this finding should be treated as tentative pending that confirmation. Given this, and the limited (non-chainstate) impact identified above, I lean toward this not clearing the bar the rules require.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L603-621)
```rust
                        SignerMessage::MockProposal(mock_proposal) => {
                            let epoch = match stacks_client.get_node_epoch() {
                                Ok(epoch) => epoch,
                                Err(e) => {
                                    warn!("{self}: Failed to determine node epoch. Cannot mock sign: {e}");
                                    continue;
                                }
                            };
                            info!("{self}: received a mock block proposal.";
                                "current_reward_cycle" => current_reward_cycle,
                                "epoch" => ?epoch
                            );
                            if epoch == StacksEpochId::Epoch25
                                && self.reward_cycle == current_reward_cycle
                            {
                                // We are in epoch 2.5, so we should mock sign to prove we are still alive.
                                self.mock_sign(mock_proposal.clone());
                            }
                        }
```

**File:** stacks-signer/src/v0/signer.rs (L2647-2658)
```rust
    /// Send a mock signature to stackerdb to prove we are still alive
    fn mock_sign(&mut self, mock_proposal: MockProposal) {
        info!("{self}: Mock signing mock proposal: {mock_proposal:?}");
        let mock_signature = MockSignature::new(mock_proposal, &self.private_key);
        let message = SignerMessage::MockSignature(mock_signature);
        if let Err(e) = self
            .stackerdb
            .send_message_with_retry::<SignerMessage>(message)
        {
            warn!("{self}: Failed to send mock signature to stacker-db: {e:?}",);
        }
    }
```

**File:** libsigner/src/v0/messages.rs (L437-446)
```rust
    /// Verify the mock proposal against the provided miner public key
    pub fn verify(&self, public_key: &StacksPublicKey) -> Result<bool, String> {
        if self.signature == MessageSignature::empty() {
            return Ok(false);
        }
        let signature_hash = self.miner_signature_hash();
        public_key
            .verify(&signature_hash.0, &self.signature)
            .map_err(|e| e.to_string())
    }
```
