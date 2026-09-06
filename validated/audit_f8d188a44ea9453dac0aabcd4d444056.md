## Analysis

The reachable analog to the Tigris "centralization freezes withdrawals/steals funds via inconsistent state" bug class is a **stale-equality check** in the Stacks-signer's tenure-duplicate guard, exactly analogous to relying on one privileged, unsynchronized checkpoint (the oracle EOA / owner) instead of the actual, currently-true state.

### Root cause

`SortitionsView::validate_tenure_change_payload` (v1 protocol path) rejects a second tenure-start ("BlockFound") proposal for the same tenure using: [1](#0-0) 

This only consults `get_last_globally_accepted_block`, i.e. it is blind to any block this same signer has already `LocallyAccepted` (signed) or `PreCommitted`. The v2 implementation was already fixed to use `get_last_signed_block` (covers locally+globally accepted), with an explicit regression test documenting the exact miss: [2](#0-1) [3](#0-2) 

The proposal-time `DuplicateBlockFound` check is documented to be the *only* place this equivocation is checked before pre-commit; it is never re-run at validate-ok or at the final signing step: [4](#0-3) 

So on the v1 protocol path, a single miner (one slot, needing no majority of signers) can:
1. Get a signer to `LocallyAccepted` (sign) tenure-start block `A` for tenure `T` (this signer's own `signed_self` is set, but `A` has not reached the 70% group threshold yet — e.g. other signers are slow/rejecting).
2. Before `A` reaches global acceptance, propose a **second, conflicting** tenure-start block `B` for the same tenure `T` (different transactions/parent choice). Because `validate_tenure_change_payload` (v1) only checks `get_last_globally_accepted_block`, it does not see `A` (LocallyAccepted, not yet global) and lets `B` pass the proposal-time duplicate check that exists specifically to prevent this.
3. `B` then proceeds to node validation and pre-commit collection exactly as any fresh proposal.

### Where the backstop is and why it's version-inconsistent

The only remaining defense is the pre-commit-time "own-tenure conflict guard" in `handle_block_pre_commit`, driven by `get_signed_conflicts`/`conflict_still_blocks`: [5](#0-4) 

This does re-examine conflicts, but the node-facing question it asks ("is the sibling still alive on the node's canonical chain?" / "TIP: own tenure confirmed at ≥ this height?") is answered against **the node's global chain state**, not against the signer's own `LocallyAccepted` bookkeeping used at proposal time. Since `A` was only `LocallyAccepted` by this one signer and never reached global consensus, the node itself never confirmed it — meaning the "own tenure confirmed" question can resolve to "no — never confirmed" or "node unreachable", both of which fall through to `SIGN` per the documented flow: [6](#0-5) 

That is precisely the designed exception: an unconfirmed local acceptance is treated as replaceable so the chain isn't stalled by a block that never made it. The proposal-time `DuplicateBlockFound` check exists specifically to prevent a signer from ever *entertaining* the replacement while its own earlier signature is still "fresh" and could plausibly still reach the threshold — and on v1 that early gate is bypassed by the `get_last_globally_accepted_block` vs `get_last_signed_block` gap.

### Consequence

For signers still negotiating protocol version 1 (`SortitionStateVersion::V1`, active whenever `determine_active_signer_protocol_version() < GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`, i.e. version < 2): [7](#0-6) 

a single miner can cause a v1 signer to place its willingness-to-sign (pre-commit) and, if the node-side freshness/backstop race resolves in the miner's favor, its actual signature over **two conflicting first-blocks of the same tenure** — breaking the one-tenure-start-per-tenure equality that `DuplicateBlockFound` is designed to enforce. This matches the report's "impact = signing a conflicting block" class (Critical), reached without needing a majority of signers, mirroring how the Tigris owner could exploit a stale/inconsistent checkpoint (their EOA price feed / minter) to break the vault's stablecoin/collateral equality.

## Recommendation
Backport the v2 fix to `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload`: replace the `get_last_globally_accepted_block` lookup with `get_last_signed_block` (or equivalent "locally-or-globally accepted" query) so the proposal-time duplicate check is consistent with what the signer has actually committed to, closing the gap before it ever reaches the node-freshness race. [1](#0-0)

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

**File:** stacks-signer/src/chainstate/v2.rs (L340-357)
```rust
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L748-756)
```rust
/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
```

**File:** docs/signer-flows.md (L248-270)
```markdown
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
    classDef good fill:#17a45c22,stroke:#1d9d5f,stroke-width:1.5px;
    classDef bad fill:#d84a3f22,stroke:#c9473d,stroke-width:1.5px;
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

**File:** stacks-signer/src/chainstate/mod.rs (L532-547)
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
    /// Uses global state version
    pub fn uses_global_state(&self) -> bool {
        match self {
            Self::V1 => false,
            Self::V2 => true,
        }
    }
```
