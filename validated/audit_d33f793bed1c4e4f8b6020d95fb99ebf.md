### Title
Fail-open RPC-failure fallback in `check_latest_block_in_tenure` lets a signer sign a stale/non-canonical block when its node connection blips at the pre-commit threshold - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_latest_block_in_tenure` treats a failed `get_tenure_tip` RPC call as if the check had passed, exactly mirroring the `PriceOracle` pattern of accepting a malformed/absent value as if it were valid data. The function is reused from three different call sites with different guarantees, and the "it's safe because the node will validate again" justification only holds for one of them, not for the recheck performed right before a signature is placed.

### Finding Description
`check_latest_block_in_tenure` is the function that answers "does this block still confirm the tip I expect?" It is invoked from all three points in the signing pipeline documented in `docs/signer-flows.md` §7: proposal arrival, validate-ok, and — critically — the final recheck in `handle_block_pre_commit` right before a signature is produced (`check_block_against_signer_db_state`, called from `stacks-signer/src/v0/signer.rs` at the pre-commit-threshold recheck, see the flow around lines 1340–1366 of `signer.rs`).

Inside the function, when the RPC call to the node fails: [1](#0-0) 

the code returns `Ok(true)` — i.e. it *assumes the proposal is higher than the tenure tip* and lets the check pass. The doc comment justifying this states: [2](#0-1) 

"If we can't look up `tenure_id`, assume `block` is higher... because this proposal ultimately must be passed to the `stacks-node` for proposal processing: so, if we pass the block height check here, we are relying on the `stacks-node` proposal endpoint to do the validation." This reasoning is true only for the *first* proposal-arrival call site, where `submit_block_for_validation` immediately re-validates the block against the node's own chain state. It is **not** true for the pre-commit-threshold recheck: at that point, per `docs/signer-flows.md` §5, the signer does **not** re-submit the block to the node's `/v3/block_proposal` endpoint — it only re-runs `check_block_against_signer_db_state` (chainstate/local-DB checks) before signing: [3](#0-2) 

So if this signer's connection to its own stacks-node (`get_tenure_tip`) fails or times out exactly when the pre-commit weight threshold is crossed, the reused fail-open branch silently reports "still confirms the tip" without ever re-verifying against the node, and the recheck proceeds to the local-DB-only conflict check (`get_signed_conflicts`), which can only catch conflicts the signer already knows about locally. A signer that is behind on StackerDB gossip (e.g., missed the broadcast of a newer canonical block that its node has already processed, perhaps due to a brief network partition) has no local record of that newer block, so `get_signed_conflicts` finds nothing, and the RPC failure removes the only other check that would have caught the staleness — the node's own tenure-tip view.

The two required equality checks the doc claims are enforced at signing time — "does it still fit the chain" and "have I already signed a rival block" — degrade to "have I already signed a rival block *that I locally know about*" whenever the tenure-tip RPC call fails at that exact moment, breaking the "approved-parent vs. canonical" invariant this function exists to enforce.

### Impact Explanation
This falls under the Critical bucket: "a signer signing an invalid, non-canonical, or conflicting block." A signer can be induced (by any transient RPC failure between the signer and its own node — not by an attacker controlling a majority, another signer's key, or the auth token) to place its signature over a block that no longer confirms the canonical tip, contributing its weight toward globally accepting a stale or conflicting block.

### Likelihood Explanation
Triggering requires only a normal, unprivileged condition: a temporary connectivity hiccup or timeout between the signer process and its local stacks-node RPC endpoint (restart, brief network blip, resource contention) landing at the moment the pre-commit weight threshold is crossed, combined with the signer being slightly behind on StackerDB gossip for the true canonical tip. No majority collusion, no key compromise, and no flooding/DoS of the network is required — this is a single-node/single-signer race between local RPC health and the pre-commit tally.

### Recommendation
Do not fail open on RPC error in `check_latest_block_in_tenure`. At minimum, the pre-commit-threshold recheck path (called from `handle_block_pre_commit` via `check_block_against_signer_db_state`) should treat an RPC failure to `get_tenure_tip` as "cannot confirm, do not sign yet" (mirroring the `ConnectivityIssues` rejection reason already used elsewhere in this file), rather than reusing the fail-open branch whose safety justification depends on a subsequent node re-validation that does not occur at this call site. If the fail-open behavior must be kept for the proposal-arrival call site, split the function so each call site's error-handling matches the guarantee that actually holds for it.

### Proof of Concept
1. Signer S receives a block proposal B1 for tenure T, submits it to its node, and gets an OK; S broadcasts a pre-commit.
2. Meanwhile, tenure T actually advances: a newer block B2 (built on B1) is globally accepted by the rest of the network, but the StackerDB message for B2 has not yet reached S (network delay/partition).
3. Enough other signers pre-commit to B1 anyway, and S's copy crosses the ≥70% pre-commit weight threshold in `handle_block_pre_commit`.
4. S's own node's RPC connection is briefly unavailable (restart, network glitch) exactly when `check_block_against_signer_db_state` → `check_latest_block_in_tenure` calls `client.get_tenure_tip(tenure_id)`.
5. The call returns `Err`, hitting the fail-open branch: [1](#0-0)  — the check reports the proposal as still valid/highest.
6. `get_signed_conflicts` finds nothing locally (S never learned of B2), so S proceeds to `SIGN: mark_locally_accepted, handle_block_signature, broadcast acceptance` over the now-stale/conflicting B1, contributing its weight toward a signature set for a block that is no longer canonical.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L366-374)
```rust
    /// Check whether or not `block` is higher than the highest block in `tenure_id`.
    ///  returns `Ok(true)` if `block` is higher, `Ok(false)` if not.
    ///
    /// If we can't look up `tenure_id`, assume `block` is higher.
    /// This assumption is safe because this proposal ultimately must be passed
    /// to the `stacks-node` for proposal processing: so, if we pass the block
    /// height check here, we are relying on the `stacks-node` proposal endpoint
    /// to do the validation on the chainstate data that it has.
    ///
```

**File:** stacks-signer/src/chainstate/mod.rs (L450-461)
```rust
        let tip = match client.get_tenure_tip(tenure_id) {
            Ok(tip) => tip.anchored_header,
            Err(e) => {
                warn!(
                    "Failed to fetch the tenure tip for the parent tenure: {e:?}. Assuming proposal is higher than the parent tenure for now.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "parent_tenure" => %tenure_id,
                );
                return Ok(true);
            }
        };
```

**File:** docs/signer-flows.md (L246-252)
```markdown
    VALID -- yes --> TH{"pre-commit weight ≥ 70%?<br/>NakamotoBlockHeader::<br/>compute_voting_weight_threshold"}
    TH -- no --> N3(["wait for more pre-commits"])
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> CONF["signed conflicts at height ≥ h,<br/>in ANY tenure<br/>get_signed_conflicts"]
    CONF --> PERM{"covered by a reorg permit whose<br/>permitting sortition is still canonical?<br/>reorg_permit_stands"}
    PERM -- yes --> EXCL(["excluded — our signature must not<br/>block a replacement we sanctioned"]):::good
```
