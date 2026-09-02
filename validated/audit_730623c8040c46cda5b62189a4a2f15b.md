### Title
Deleting a multisig member does not purge that member's stale confirmations from other pending requests, allowing a request to execute below the intended K-of-N threshold - (File: multisig2/src/lib.rs / multisig/src/lib.rs)

### Summary
`delete_member` (multisig2) and `DeleteKey` handling (multisig) only remove pending *requests created by* the removed member, and only strip confirmations belonging to those specific requests. They never scan the `confirmations` map for other pending requests that the removed member had merely *confirmed* (but not created). A stale confirmation entry from a member who has since been removed therefore remains counted toward `num_confirmations` on any request that member had confirmed before being deleted, letting that request later execute with fewer genuine confirmations from current members than the configured threshold requires - directly mirroring the reported bug class of a state-changing operation that fails to reset related pending state (`_resetAmendmentParams`) before further processing continues.

### Finding Description
In `multisig2/src/lib.rs`, `delete_member` cleans up only requests the deleted member *authored*: [1](#0-0) 

It filters `self.requests` by `r.member == member` (the request's *creator*), removing those requests and their confirmation sets. It never iterates `self.confirmations` for requests created by *other* members where the deleted member had simply called `confirm`. That stale entry (keyed by the now-removed member's identity string) stays in the `HashSet<String>` for those other requests.

`confirm` later just compares the size of that (potentially stale-inflated) set against `num_confirmations`: [2](#0-1) 

There is no re-validation that every entry in `confirmations` still corresponds to a `current_member()` - the check only ensures the *new* confirmer is a live member, not that prior recorded confirmations are still valid.

The equivalent v1 contract has the same gap in `DeleteKey` handling, which only removes requests where `r.signer_pk == pk` (the request creator's key): [3](#0-2) 

The binding that should hold is: `confirmations counted for request R == number of currently-live members who approved R`. The bug breaks this equality by letting a removed member's phantom approval persist and count toward the threshold.

### Impact Explanation
This is Critical under "a multisig request executed below threshold." A pending transfer, function call, or key/member change can execute with strictly fewer live-member approvals than the multisig's configured `num_confirmations`, undermining the K-of-N custody guarantee the contract is supposed to enforce over the account's NEAR balance and access keys.

### Likelihood Explanation
This requires only the ordinary governance sequence that already occurs in normal multisig operation: (1) a member confirms a pending request without pushing it to execution, (2) that member is later removed via a legitimate `DeleteMember`/`DeleteKey` action, and (3) a remaining member subsequently confirms the same still-pending request. No malicious collusion or privilege escalation is needed beyond actions the multisig's own design already permits members to take (confirming/creating requests, and voting to remove a member) - the flaw is purely in the missing state-reset, not in any single actor's bad behavior.

### Recommendation
When a member/key is deleted, iterate all pending requests in `self.requests` and, for each, remove the deleted member's entry from the corresponding `self.confirmations` set (not just for requests that member authored). Alternatively, validate on `confirm`/execution that every entry in a request's confirmation set still corresponds to a member present in `self.members` (filtering out stale entries before comparing the count to `num_confirmations`).

### Proof of Concept
1. Deploy `multisig2` with `members = {A, B, C, D}`, `num_confirmations = 3`.
2. `A` calls `add_request(R)` (transfer). `confirmations[R] = {}`.
3. `B` calls `confirm(R)` → `confirmations[R] = {B}` (len 1, below threshold).
4. `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (len 2, below threshold).
5. Via a separate, already-quorum-approved request, `B` is removed with `DeleteMember{member: B}`. `delete_member` only removes requests where `r.member == B`; since `R` was created by `A`, it is untouched, and `confirmations[R]` still contains `"B"`. Members are now `{A, C, D}` (3 ≥ `num_confirmations`, so the removal succeeds).
6. `D` calls `confirm(R)`: `confirmations[R].len() (2) + 1 == 3 >= num_confirmations (3)` → the request executes, transferring funds, even though only `C` and `D` are current members who genuinely approved it - one fewer live approval than the configured 3-of-4 threshold.

### Citations

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L356-374)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
```

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```
