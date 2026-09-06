## Title
Signer double-signs a conflicting block in its own tenure when the node RPC call fails during pre-commit re-check - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
In `handle_block_pre_commit`, once a proposal crosses the 70% pre-commit weight threshold, the signer re-checks whether any previously-signed conflicting block in the *same tenure* still blocks the new one by calling `stacks_client.get_tenure_tip(...)`. If that RPC call errors, the code does not refuse to sign; it logs a warning and falls through to sign the new (conflicting) block anyway. This mirrors the reported bug class: an external/dependent call whose failure path is not safely handled, causing the caller to proceed as if nothing is wrong — except here the consequence is not "funds stuck," but a signer placing its signature on two conflicting blocks in the same tenure (equivocation).

### Finding Description
The relevant code: [1](#0-0) 

```
if conflicts.iter().any(|conflict| {
    conflict.consensus_hash == block_info.block.header.consensus_hash
        && !self.reorg_permit_stands(stacks_client, conflict)
}) {
    match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
        Ok(tip) => {
            let tip_height = tip.anchored_header.height();
            if tip_height >= block_info.block.header.chain_length {
                warn!(... "Refusing to sign.");
                return;
            }
        }
        Err(e) => {
            warn!(... "Treating the tenure as unconfirmed.");
            // falls through -- no return here
        }
    }
}
...
// proceeds to mark_locally_accepted / sign
```

When the pending block conflicts with an earlier signed block *in the same tenure* (a same-tenure sibling — the exact scenario the "duplicate block" guard exists to catch, per `docs/signer-flows.md` section 5), the signer asks the node for that tenure's canonical tip height to decide whether the conflict is still live. On the `Ok` path, if the tip is already at/above the new block's height, it correctly refuses to sign. But on the `Err` path (RPC failure/timeout/connectivity issue to the local `stacks-node`), the code treats the tenure as "unconfirmed" and simply continues — ultimately reaching `mark_locally_accepted` and broadcasting a signature.

This is explicitly called out as intentional in the design docs: [2](#0-1) 

The doc states the *general* rule for conflict resolution is "whenever the node cannot be asked, the conflict keeps blocking... wrongly signing cannot be taken back," but then carves out this specific own-tenure branch as an exception where an unreachable node makes the signature "go out" instead. This is inconsistent with every other node-failure branch in the same function (`reorg_permit_stands`, `conflict_still_blocks`, the `LIVE`/`SORT` questions), which all fail closed (keep blocking / refuse to sign) on RPC failure. The `check_latest_block_in_tenure` "assume higher" default elsewhere is safe because it is backstopped by the node's own proposal-validation endpoint (`docs/signer-flows.md` lines 366-373), but there is no such backstop for signature issuance itself — once the signer signs, the equivocation cannot be undone even if the node would have rejected the resulting block. [3](#0-2) 

### Impact Explanation
This breaks the one-signature-per-height/tenure invariant: the signer can place a valid signature on two conflicting blocks within the same tenure. If enough signers hit the same RPC-failure condition (e.g., during a `stacks-node` restart, local resource exhaustion, or a targeted disruption of the signer's `/v3/tenures/tip` endpoint at the moment the second sibling crosses pre-commit threshold), the signer set could produce signatures for both siblings, which is exactly the equivocation the own-tenure conflict guard was added to prevent (see `signer_refuses_to_sign_second_sibling_tenure_start` test). Per the task's severity classification, "a signer signing a ... conflicting block" is Critical.

### Likelihood Explanation
Requires the local `stacks-node` RPC call (`get_tenure_tip`) to fail at the precise moment the pre-commit threshold is reached for a same-tenure conflicting block — a condition that can occur from ordinary connectivity/timeout issues (not requiring a majority of signers or key compromise), similar in spirit to the low-likelihood-but-real trigger condition in the source report (a special wallet type). A miner presenting a same-tenure sibling block combined with any transient RPC hiccup on the signer's node is sufficient; no other signer's cooperation or key is needed.

### Recommendation
Make the `Err` branch of the `get_tenure_tip` call in `handle_block_pre_commit`'s own-tenure conflict check fail closed (refuse to sign / return), consistent with every other node-unreachable branch in this function (`reorg_permit_stands`, `conflict_still_blocks`). This sacrifices some liveness (the block can be resubmitted once the node is reachable) in exchange for eliminating a lossy, irreversible equivocation risk, matching the same recommendation pattern as the source report ("remove the special-cased shortcut that fails open").

### Proof of Concept
1. A one-slot miner proposes and gets block A signed by the signer set at height H in tenure T (recorded via `mark_locally_accepted`/`mark_globally_accepted`).
2. Before A is broadcast/adopted by all nodes, the miner proposes a conflicting sibling block B at the same height H in the same tenure T; B reaches the pre-commit threshold (70% weight) on a given signer.
3. At the moment that signer runs the same-tenure conflict re-check in `handle_block_pre_commit`, its call to `stacks_client.get_tenure_tip(&consensus_hash)` fails (network blip, node restart, local resource pressure).
4. The code logs a warning and falls through the `Err` arm without returning, and the signer proceeds to `mark_locally_accepted`/sign B — despite already having signed the conflicting A in the same tenure.
5. If this repeats across enough signers (or timing coincides across the set), signatures accumulate for both A and B, producing two globally-signed, mutually conflicting blocks in the same tenure.

### Citations

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

**File:** docs/signer-flows.md (L329-341)
```markdown
Whenever the node cannot be asked, the conflict keeps blocking: that only delays
the replacement until the signature goes stale, whereas wrongly signing cannot be
taken back. The one recorded exception is a tenure whose reorg we sanctioned
under the reorg-timing rules (section 8): there the node still serves the
conflict as fully live — replacing it is only legitimate because we permitted it
— so no question asked of the node about the _conflict_ could clear it. Instead
the record carries the permitting tenure's sortition, and `reorg_permit_stands`
asks the node whether that sortition is still canonical: while it is, the
conflict is excluded outright; if a burnchain fork orphaned it, the reorg we
sanctioned can no longer happen and the conflict gets its voice back. A false
404 there needs no tip-height guard — it merely restores a conflict, which at
worst delays the replacement. For the own-tenure question below, an unreachable
node is instead treated as unconfirmed and the signature goes out.
```

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
