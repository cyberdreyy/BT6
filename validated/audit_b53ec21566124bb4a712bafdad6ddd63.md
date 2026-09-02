### Title
Multisig request can execute below the required live-member threshold because stale confirmations from removed members are not purged - ([File: multisig2/src/lib.rs])

### Summary
`delete_member()` in `multisig2/src/lib.rs` only removes pending requests and confirmations that were *created* by the removed member; it never scans the `confirmations` map to strip confirmations that the removed member cast on *other* requests. `confirm()` then compares `confirmations.len()` directly against `self.num_confirmations` without re-validating that every recorded confirmer is still a current member. As a result, a request can be executed with fewer live-member confirmations than the configured threshold, because a stale confirmation from a since-removed member is still counted.

### Finding Description
`confirm()` treats the size of the `confirmations` set as ground truth for how many members have approved a request: [1](#0-0) 

`delete_member()` is the only place that reconciles membership changes with outstanding requests, but its cleanup is scoped solely to requests whose *creator* (`r.member`) equals the member being removed: [2](#0-1) 

If member `B` confirmed (but did not create) request `R`, and `B` is later removed via `DeleteMember`, the loop `self.requests.iter().filter_map(|(k, r)| if r.member == member ...)` does not match `R` (since `R.member` is the creator, e.g. `A`, not `B`). Consequently `R`'s `confirmations` set (still containing `B`'s entry) is left untouched. `assert_valid_request()` only checks that the *current caller* is a live member, not that previously recorded confirmations are: [3](#0-2) 

This mirrors the reported bug class: a security-critical decision (`canHedge`/here, "is this request sufficiently confirmed") is computed from a stale snapshot (`confirmations` set) that ignores an intervening state change (member removal), producing a wrong result — in this case a false positive that authorizes execution.

The binding broken is: **confirmations counted == live members who approved**. After `B` is removed, the equality becomes confirmations counted (includes `B`) != live members who approved (excludes `B`), yet `confirm()` still treats the stale count as valid.

### Impact Explanation
This is Critical impact per the specified categories: "a multisig request executed below threshold." A request (e.g. a `Transfer`, `AddKey`, or another `DeleteMember`/`AddMember` action) can be pushed through `execute_request()` with fewer *actual current* member confirmations than `num_confirmations` requires, because one or more of the counted confirmations belong to accounts/keys that are no longer members. This undermines the fundamental security guarantee of the K-of-N multisig scheme and can lead to unauthorized transfers of NEAR/wNEAR or unauthorized key/member changes.

### Likelihood Explanation
This is reachable through ordinary, unprivileged multisig operation flow with no need for a compromised key, a redeploy, or foundation/owner privileges beyond being (at some point) a legitimate member — a completely realistic operational sequence:
1. Member `A` creates request `R` (`add_request`).
2. Member `B` confirms `R` (`confirm`), but confirmations remain below threshold, so `R` stays pending.
3. Later, through a normal, fully-confirmed `DeleteMember` request, `B` is legitimately removed from the multisig (e.g., they left the organization or their key was rotated).
4. `B`'s stale confirmation on `R` is never purged.
5. Any other live member confirms `R`; the counted confirmations (including `B`'s) reach `num_confirmations`, and `R` executes — despite one fewer live member than intended having approved it.

This requires only the normal lifecycle of request creation, confirmation, and member removal — no attacker privilege escalation, no reliance on a compromised key being used post-removal (the stale entry is just a string in a `HashSet`, counted regardless of whether the underlying key/account is still valid).

### Recommendation
When a member is deleted, iterate over **all** outstanding requests' `confirmations` sets (not just those the removed member created) and remove that member's confirmation entry from each. Alternatively, change `confirm()` to only count confirmations belonging to entries currently present in `self.members` (filter live members) before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. As `A`: `add_request(R)` (transfer/critical action) — creates `R` with `member: A`, empty confirmations.
3. As `B`: `confirm(R)` — `confirmations[R] = {B}` (1 confirmation, threshold 3, not yet executed).
4. Through the normal multisig flow (a separate, fully-confirmed request), remove `B`: `DeleteMember { member: B }`, executed via `execute_request` → `delete_member(promise, B)`. Since `R.member == A ≠ B`, `R` and its confirmations (`{B}`) are **not** cleaned up.
5. Members are now `{A, C, D}`.
6. As `A`: `confirm(R)` → `confirmations[R] = {B, A}`, length 2, `2+1 (checked as len+1) ... ` — actually per code, `confirmations.len() as u32 + 1 >= num_confirmations` is evaluated *before* inserting the new confirmer, so after `A` confirms, len becomes 2 (`{B,A}`), and the check `1+1 >= 3` fails, needing one more.
7. As `C`: `confirm(R)` → check `2 (len of {B,A}) + 1 >= 3` → true → `R` executes.
8. `R` executed with confirmations effectively from `A`, `C`, and the stale `B` — only 2 of the 3 confirmers (`A`, `C`) are still live members; `B` is no longer a member. The request was executed below the true live-member threshold. [4](#0-3) [5](#0-4)

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

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```
