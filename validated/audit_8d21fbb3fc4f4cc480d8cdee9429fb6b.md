## Analysis

I found a valid analog to the "check applied to one side of an operation but not the other" bug class, in `multisig2`'s member-removal logic.

### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member()` in `multisig2/src/lib.rs` only purges pending requests where the removed member was the *original request signer* (`r.member == member`), but it never scans or cleans the `confirmations` set of *other* still-pending requests that the removed member had already confirmed. Because `confirm()` counts entries in that `confirmations` HashSet purely by identity string, a member's earlier confirmation continues to count toward `num_confirmations` even after that member has been permanently removed from the multisig, and even after their access key has been deleted on-chain.

### Finding Description
The custody binding that should hold is: **confirmations counted == confirmations from currently live members**. `confirm()` trusts `self.confirmations.get(&request_id)` blindly to determine whether the threshold has been met: [1](#0-0) 

`delete_member()` removes the member from `self.members` and deletes their access key, but only cleans confirmations/requests for the subset of requests they *originated* (`r.member == member`): [2](#0-1) 

If member `M` had earlier called `confirm()` on a request `R` originated by a *different* member, `M`'s entry stays in `self.confirmations[R]` forever — `delete_member` never iterates `self.confirmations` to strip `M`'s vote from requests they didn't originate. Once `M` is removed, the live member set has shrunk, but the stale confirmation from `M` is still tallied in `confirm()`'s `confirmations.len() as u32 + 1 >= self.num_confirmations` check. This lets a request execute with fewer *live* confirming members than `num_confirmations` mandates — breaking the equality `confirmations from live members == num_confirmations`.

### Impact Explanation
This falls under the "Critical" bucket described by the scope rules: *a multisig request executed below threshold*. An attacker (or a set of members who no longer control quorum after a member's removal) can execute a `Transfer`, `FunctionCall`, `AddKey`, etc. action while only a minority of the currently live membership actually confirmed it, because a removed member's stale vote is still tallied.

### Likelihood Explanation
Likelihood is realistic in the normal operational flow of the multisig, requiring no privileged action beyond the ordinary "remove a member" governance action already supported by the contract:
1. Member `M` confirms request `R1` (created by another member) but does not push it over threshold.
2. Multisig votes to remove `M` via `DeleteMember` — this succeeds because `delete_member` only checks requests where `M` is the *signer*, not the *confirmer*, so `R1` and `M`'s vote on it survive untouched.
3. Remaining members confirm `R1` further; `M`'s stale confirmation is added to the tally even though `M` is no longer a member, allowing execution below the true live-member threshold.

### Recommendation
When removing a member in `delete_member`, iterate over `self.confirmations` (not just `self.requests` filtered by originating signer) and strip the removed member's identity string from every pending request's confirmation set, decrementing/re-evaluating threshold state as needed — mirroring what the recommended patch did for the USDz analog (extend the check to cover both sides of the operation, here: both "who originated" and "who confirmed").

### Proof of Concept
Conceptual reproduction using the contract's own test harness pattern (`multisig2/src/lib.rs` tests, e.g. `test_multi_add_request_and_confirm`, `add_key_delete_key_storage_cleared`):
1. Init multisig with members `[A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request` for `Transfer{amount}` with `receiver_id` unrelated to `D`.
3. `D` calls `confirm(request_id)` → confirmations = `{A(if auto-confirmed), D}` (say 1 so far, doesn't hit threshold).
4. Members vote `DeleteMember{D}` and execute it (requires only that `members.len()-1 >= num_confirmations`, i.e., 3 members remain ≥ 3 — allowed). `D`'s access key is deleted; `D` is removed from `self.members`. Note `delete_member` does **not** touch `self.confirmations[request_id]`, so `D`'s vote remains.
5. `B` and `C` confirm the same `request_id`. `confirmations.len()` becomes 3 (`D`, `B`, `C`) which `>= num_confirmations (3)`, and the transfer executes — even though `D` is no longer a member and only 2 *live* members (`B`, `C`) plus a non-existent one actually authorized it. [3](#0-2) [4](#0-3)

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
