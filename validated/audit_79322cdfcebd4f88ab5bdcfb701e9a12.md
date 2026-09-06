Confirmed: this is a real, reachable safety bug in `handle_block_pre_commit` / `handle_block_validate_ok`.

### Title
Signer signs and broadcasts acceptance for a block already marked `GloballyRejected`, via a late `BlockValidateOk` racing peer-gossiped rejections - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_validate_ok`'s "skip if already decided" guard only checks `block_info.valid.is_some()`, not `block_info.state`. Peer-gossiped rejections that reach the 30% blocking-minority threshold set `state = GloballyRejected` via `mark_globally_rejected()` (`stacks-signer/src/signerdb.rs:304-306`) without ever touching `valid`. If this signer's own node validation for the same block returns `Ok` afterward, the guard is bypassed, and the subsequent `mark_pre_committed`/`mark_locally_accepted` calls mutate `valid`/`signed_self` *before* their internal `move_to` state-check fails, and the calling code treats that failure as benign whenever `has_reached_consensus()` is true - letting the signer broadcast a signature for a block it has already globally rejected.

### Finding Description
The broken equality: the code assumes `block_info.valid.is_some() == "we've already finalized a verdict for this block"`. That equality does not hold when the verdict came from peer consensus (`GloballyRejected`/`GloballyAccepted`) rather than from this signer's own validation, because `mark_globally_rejected()` only calls `move_to(BlockState::GloballyRejected)` and never sets `valid` (`stacks-signer/src/signerdb.rs:304-306`, vs. `mark_locally_rejected` at :298-301 which does set `valid = Some(false)`).

Exploit flow:
1. An attacker (single miner slot) proposes a block `B`. This signer submits it for validation (`submit_block_for_validation`) and is waiting; `block_info.valid == None`, `state == Unprocessed`.
2. Other signers, for any reason (genuine conflict, timing, or attacker-influenced timing), broadcast `BlockRejection`s for `B`. `handle_block_rejection` → `store_and_process_block_rejection` (`stacks-signer/src/v0/signer.rs:2268-2369`) tallies weight; once rejection weight crosses the blocking-minority threshold, it calls `block_info.mark_globally_rejected()` and persists `state = GloballyRejected`, leaving `valid == None`.
3. The node's `/v3/block_proposal` finally returns `Ok` for `B` (validation was legitimately fine or delayed). `handle_block_validate_ok` (`stacks-signer/src/v0/signer.rs:1882-1985`) loads the same `block_info`, checks `if block_info.valid.is_some() { ...ignore... }` (line 1932) — this is `false`, so processing continues.
4. Assuming `check_block_against_signer_db_state` still passes, `block_info.mark_pre_committed()` is called (`signerdb.rs:272-277`): it unconditionally sets `self.valid = Some(true)` and `approved_time`, *then* calls `move_to(PreCommitted)`, which fails `check_state` because the previous state is `GloballyRejected` (only `Unprocessed → PreCommitted` is legal, `signerdb.rs:313-329`). `mark_pre_committed` returns `Err`, but `valid` has already been permanently flipped to `Some(true)`.
5. Back in `handle_block_validate_ok` (lines 1961-1970): the `Err` branch checks `if !block_info.has_reached_consensus() && block_info.state != LocallyAccepted { return; }`. Since `state` is still `GloballyRejected` and `has_reached_consensus()` returns `true` for that state, the guard is skipped and execution falls through to `send_block_pre_commit` + `self.handle_block_pre_commit(...)` for its own pre-commit.
6. `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs:1290-1479`) checks `signed_self.is_some()` (false) and `valid.unwrap_or(false)` (now `true`, from step 4's corrupted mutation) — passes. If pre-commit weight threshold and conflict checks pass, it calls `block_info.mark_locally_accepted(false)` (`signerdb.rs:280-289`), which again unconditionally sets `valid = Some(true)`, `approved_time`, and `signed_self` *before* `move_to(LocallyAccepted)` fails (since `GloballyRejected` is excluded, `signerdb.rs:321-324`). The `Err` from `mark_locally_accepted` is swallowed identically via the same `has_reached_consensus()` escape hatch (lines 1467-1471), and the function unconditionally proceeds to `create_block_acceptance`, `handle_block_signature`, and `send_block_response` — **broadcasting a signed acceptance for block `B`, which this signer's own persistent DB state simultaneously records as `GloballyRejected`.**

Existing guards fail because: (a) the "already decided" check in `handle_block_validate_ok` keys off `valid`, not `state`/`has_reached_consensus()`; (b) `mark_pre_committed`/`mark_locally_accepted` mutate fields before validating the transition, so a failed `move_to` still leaves side effects; (c) the callers treat `has_reached_consensus() == true` as a reason to *suppress the warning and continue*, when it should instead be a hard stop, since it is precisely the case that must never be overridden.

### Impact Explanation
This breaks the "rejection recounted as acceptance" / signature validity guarantee (Critical): a signer can be made to sign and gossip an `Accepted` `BlockResponse` for a block its own signer DB has terminally marked `GloballyRejected`. This corrupts the equivocation/consensus bookkeeping other signers and the miner-side `SignerCoordinator` rely on (weight tallies in `stacks-node/src/nakamoto_node/signer_coordinator.rs`), potentially contributing signature weight toward a block the network already decided to reject, and is repeatable for every block proposal where this signer's own validation round-trip is slower than the time it takes peers to cross 30% rejection weight.

### Likelihood Explanation
Preconditions: (1) this signer must have a validation request outstanding (`submitted_block_proposal` in flight) for block `B`; (2) other signers' rejections for `B` must cross the blocking-minority (>30%) threshold before this signer's own node responds; (3) `check_block_against_signer_db_state` must not itself flag `B` as inconsistent at validate-ok time. Precondition (1)+(2) is a normal timing race that requires no signer-key compromise or majority control - only ordinary network/validation latency variance, which an attacker with a single miner slot can amplify (e.g., by proposing a block whose validation is slow on this signer's node, or by timing gossip) while other signers reject for legitimate reasons. No privileged capability beyond "propose one block, observe gossip" is required, and the race is repeatable per proposal.

### Recommendation
- In `handle_block_validate_ok`/`handle_block_validate_reject`/`handle_block_pre_commit`, gate on `block_info.has_reached_consensus()` (or `state` directly) in addition to/instead of `valid.is_some()`, and return immediately without any further mutation when the block has already reached global consensus.
- Make `mark_pre_committed`/`mark_locally_accepted`/`mark_locally_rejected` transactional: only mutate `valid`/`approved_time`/`signed_self` after `move_to` succeeds (or roll back the state-independent fields on `Err`), so a failed transition can never leave `valid`/`signed_self` inconsistent with `state`.
- Remove the "suppress warning because `has_reached_consensus()`" pattern in the callers (`stacks-signer/src/v0/signer.rs` around lines 1467-1471 and 1961-1970 and 1355-1359) and replace with an explicit early `return` whenever `move_to` fails due to the block already being in a terminal global state.

### Proof of Concept
Rust test (add to `stacks-signer/src/v0/tests.rs`, mirroring existing `run_sibling_scenario` helpers):

```rust
#[test]
fn late_validate_ok_after_global_rejection_does_not_flip_to_accept() {
    // 1. Construct a signer + block proposal B; drive it to Unprocessed with
    //    submitted_block_proposal set (simulate node validation in flight).
    // 2. Feed enough SignerMessage::BlockResponse(Rejected) events from other
    //    signer addresses (via handle_block_rejection) to cross the 30%
    //    blocking-minority weight, and assert:
    let info_before = signer.signer_db.block_lookup(&hash_b).unwrap().unwrap();
    assert_eq!(info_before.state, BlockState::GloballyRejected);
    assert_eq!(info_before.valid, None);

    // 3. Deliver the delayed BlockValidateResponse::Ok for the same hash via
    //    process_event / handle_block_validate_response.
    signer.process_event(&client, &mut sortition, Some(&validate_ok(&hash_b)), &tx, 1);

    let info_after = signer.signer_db.block_lookup(&hash_b).unwrap().unwrap();

    // ASSERTIONS on both sides of the equality:
    assert_eq!(
        info_after.state, BlockState::GloballyRejected,
        "state must remain terminal"
    );
    assert_eq!(
        info_after.valid, None,
        "valid must NOT be corrupted to Some(true) once globally rejected"
    );
    assert!(
        info_after.signed_self.is_none(),
        "signer must NOT sign a block it already globally rejected"
    );
    // Also assert no BlockResponse::Accepted or BlockPreCommit was broadcast
    // for hash_b in the captured outbound messages from step 3.
}
```
Expected (buggy) result without the fix: `info_after.valid == Some(true)`, `signed_self.is_some() == true`, and an `Accepted` `BlockResponse`/`BlockPreCommit` is broadcast for `hash_b`, contradicting `state == GloballyRejected`.