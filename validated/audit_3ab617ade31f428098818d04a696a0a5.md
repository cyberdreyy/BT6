Based on my investigation, I was unable to retrieve the exact SQL body of `SignerDb::add_block_rejection_signer_addr` (the tool budget ran out before I could pull those specific lines), so I can only ground this finding in the table schema itself, which is concrete. I'm flagging this uncertainty explicitly.

### Title
Rejection-tracking table keyed only by `signer_addr` collapses per-block rejection records across different blocks - (File: stacks-signer/src/signerdb.rs)

### Summary
The `block_rejection_signer_addrs` table, which records which signer addresses rejected which block (by `signer_signature_hash`), is defined with `PRIMARY KEY (signer_addr)` instead of a composite key over `(signer_signature_hash, signer_addr)`. This means a single signer address can have at most one row in this table at any time, globally, rather than one row per block it rejected.

### Finding Description [1](#0-0) 

defines:
```
CREATE TABLE IF NOT EXISTS block_rejection_signer_addrs (
    signer_signature_hash TEXT NOT NULL,
    signer_addr TEXT NOT NULL,
    PRIMARY KEY (signer_addr)
) STRICT;
```

Unlike the analogous acceptance-tracking mechanism (`block_signatures`, indexed on `signer_signature_hash` per [2](#0-1) , which naturally supports one signature per `(hash, signer)` pair), the rejection table's key omits `signer_signature_hash` entirely. Consequently, if the same signer address rejects two *different* blocks (e.g. two competing siblings at the same height in different tenures, or a rejected block followed later by a rejection of an unrelated proposal), only one of those rejection rows can exist at a time — the second write either overwrites the first (if inserted with `OR REPLACE`) or is rejected/ignored (if inserted with `OR IGNORE`, or errors on a plain `INSERT` against a `STRICT` table's PK constraint).

This is used from `handle_block_rejection` in the pre-commit/response path (per `docs/signer-flows.md` anchors: `handle_block_rejection`, `store_and_process_block_rejection`, `add_block_rejection_signer_addr`) to tally the rejecting weight for a block and decide whether >30% weight has rejected it, at which point the local signer marks that block `GloballyRejected` [3](#0-2) . Because the storage layer cannot hold more than one `(signer_addr → block)` rejection mapping simultaneously, the weight tally for any given `signer_signature_hash` can silently lose real rejections cast by addresses that have since rejected (or, depending on conflict semantics, previously rejected) a different block.

### Impact Explanation
This breaks the equality between "actual aggregated rejection weight cast by the signer set for block X" and "rejection weight recorded/tallied in signerdb for block X" — the same class of bug as the WEBRick smuggling report (an ambiguous/insufficiently-keyed record causes two views of the same event stream to diverge). The practical effect here is undercounting: a signer's local bookkeeping can understate the true rejecting weight for a block whose rejectors have since rejected another block, which can prevent that signer from ever concluding a conflicting block is `GloballyRejected`. Since large parts of the signing state machine (`conflict_still_blocks`, freshness/staleness reasoning for signed conflicts) rely on this state to eventually stop treating a dead sibling as blocking, an undercount here risks wedging the signer into refusing to sign a legitimate replacement block indefinitely — matching the allowed "High: signer wedged into never signing valid blocks" impact class.

### Likelihood Explanation
Triggering this only requires a single miner/proposer to produce two competing block proposals at overlapping heights (siblings, or sequential distinct rejected proposals) that the same signer set rejects — a routine, low-cost, one-slot-miner-plus-gossip scenario, not requiring a majority of signers or any key compromise.

### Recommendation
Change the `block_rejection_signer_addrs` primary key to the composite `(signer_signature_hash, signer_addr)`, mirroring how `block_signatures` and `block_rejection_signer_addrs`' own index (`block_rejection_signer_addrs_on_block_signature_hash`, per `CREATE_INDEXES_3`) already assume per-block scoping.

### Proof of Concept
Not independently executed (tool budget exhausted before I could confirm the exact `INSERT` statement used by `add_block_rejection_signer_addr`). Conceptually:
1. Signer S rejects block A (`signer_signature_hash = H_A`) → row `(H_A, S)` inserted.
2. A different/competing proposal B (`H_B`) at the same or a later height is rejected by S as well → the insert for `(H_B, S)` either replaces or is blocked by the existing `(H_A, S)`/`(*, S)` row, since `signer_addr` alone is the primary key.
3. A query for "who rejected H_A" (used to compute rejection weight for A) no longer includes S if the row was overwritten, undercounting A's true rejection weight — or symmetrically, S's rejection of B is dropped if `INSERT OR IGNORE` is used.

I was not able to confirm the exact conflict-resolution clause (`REPLACE`/`IGNORE`/error) in the time available, so the precise failure mode (which block's rejection is lost) is uncertain, but the schema itself guarantees that at most one `(signer_addr, block)` rejection can be retained at a time, which is the root defect.

### Citations

**File:** stacks-signer/src/signerdb.rs (L423-425)
```rust
static CREATE_INDEXES_2: &str = r#"
CREATE INDEX IF NOT EXISTS block_signatures_on_signer_signature_hash ON block_signatures(signer_signature_hash);
"#;
```

**File:** stacks-signer/src/signerdb.rs (L514-524)
```rust
static CREATE_BLOCK_REJECTION_SIGNER_ADDRS_TABLE: &str = r#"
CREATE TABLE IF NOT EXISTS block_rejection_signer_addrs (
    -- The block sighash commits to all of the stacks and burnchain state as of its parent,
    -- as well as the tenure itself so there's no need to include the reward cycle.  Just
    -- the sighash is sufficient to uniquely identify the block across all burnchain, PoX,
    -- and stacks forks.
    signer_signature_hash TEXT NOT NULL,
    -- the signer address that rejected the block
    signer_addr TEXT NOT NULL,
    PRIMARY KEY (signer_addr)
) STRICT;"#;
```

**File:** docs/signer-flows.md (L368-371)
```markdown
    KIND -- "Rejected" --> HBR["handle_block_rejection:<br/>verify, store via<br/>add_block_rejection_signer_addr"]
    HBR --> RT{"rejection weight makes<br/>70% approval impossible?"}
    RT -- no --> N3(["wait"])
    RT -- yes --> GREJ["mark_globally_rejected;<br/>pre-global-state versions also<br/>update miner status"]:::bad
```
