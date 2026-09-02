### Title
Stale confirmations from removed multisig members remain counted toward `num_confirmations`, allowing request execution below the live-member threshold - (File: multisig2/src/lib.rs)

### Summary
In `MultiSigContract::delete_member`, when a member is removed from the multisig, only requests *originated by* that member are purged; confirmations that member cast on requests originated by *other* members are never removed from the `confirmations` map. Because `confirm()` counts entries in that stale set toward `num_confirmations`, a request can execute using a vote from an account that is no longer a member, effectively lowering the live K-of-N threshold below what governance intends.

### Finding Description
The multisig tracks approvals per request in `confirmations: LookupMap<RequestId, HashSet<String>>` and executes a request once `confirmations.len() + 1 >= self.num_confirmations` in `confirm()`. [1](#0-0) 

When a member is removed via `MultiSigRequestAction::DeleteMember` → `delete_member`, the code only scans `self.requests` for requests where `r.member == member` (i.e., requests the removed member itself *created*), deleting those requests and their confirmation sets: [2](#0-1) 

It never scans `self.confirmations` for entries where the removed member's identifier is present as a *confirmer* (but not the request creator) on some other pending request. Those stale confirmation strings remain in the `HashSet<String>` and continue to count toward the `num_confirmations` threshold on the next `confirm()` call.

`assert_valid_request`, used by both `confirm` and `delete_request`, only validates that the *caller* is currently a member; it performs no re-validation of the existing confirmer set against current membership: [3](#0-2) 

The custody binding that should hold is: `confirmations counted == confirmations from current live members`. This is broken because a removed member's earlier vote persists in the set and is still counted.

### Impact Explanation
This allows a `MultiSigRequestAction::Transfer` (or `AddKey`/`FunctionCall`/any other action) to be executed with fewer *live* member approvals than the configured `num_confirmations` — e.g., with `num_confirmations = 2` and members `{A, B, C}`: A creates and confirms a Transfer request (`confirmations = {A}`); the multisig later (legitimately) removes A via `DeleteMember`; the Transfer request A created is untouched by `delete_member` since it filters only by `r.member == member` where `member` is the one being deleted, but this filter only removes requests **A created**, not confirmations A cast elsewhere — wait, in this exact walk the request was created by A so it is removed. The bug specifically manifests when A merely *confirms* a request created by a different member, e.g. B creates the Transfer request, A confirms it (`confirmations = {A}`), A is then removed as a member. The request survives untouched (its creator is B, not A), and its stale confirmation set still contains A. C then confirms: `confirmations.len() + 1 == 2 >= num_confirmations`, and the request executes funded by only one truly live approval (C), using A's revoked vote to satisfy the threshold. This is a request executed below the intended live-member threshold, matching the Critical impact category "a multisig request executed below threshold."

### Likelihood Explanation
No privileged attacker action is required beyond ordinary multisig usage: any member confirming a request that is later left pending while that confirming member is removed (a routine operational event — key rotation, compromised key removal, member offboarding) creates the exploitable state. Any remaining live member(s) can then push the stale-inflated count over threshold with fewer genuine votes than `num_confirmations` mandates.

### Recommendation
In `delete_member`, iterate `self.confirmations` for all pending requests and strip the removed member's entry from each confirmation set (not just requests the member created), or re-validate at `confirm()`/execution time that every entry in a request's confirmation set corresponds to a currently live member, discarding stale entries before counting.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2`.
2. As member B, call `add_request` with a `Transfer` action (request_id = 0). `confirmations[0] = {}`.
3. As member A, call `confirm(0)`. Now `confirmations[0] = {A}` (1 of 2 needed).
4. Members confirm and execute a `DeleteMember { member: A }` request through the normal K-of-N flow, removing A from `self.members` and deleting A's key/account. Per `delete_member`, only requests where `r.member == A` (i.e., created by A) are purged — request 0 (created by B) is untouched, and `confirmations[0]` still equals `{A}`. [4](#0-3) 
5. As member C, call `confirm(0)`. `confirmations.len() + 1 == 2 >= num_confirmations(2)` succeeds and the Transfer executes, even though A is no longer a member and only C's vote is currently live. [5](#0-4)

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
