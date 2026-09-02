### Title
Stale confirmations from removed multisig members allow request execution below the confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts confirmations stored in a `HashSet<String>` without re-validating that each recorded confirmer is still a current multisig member. `delete_member` only purges confirmations/requests that were *created* by the removed member, not confirmations that member previously cast on other members' requests. This lets a request execute with fewer genuinely live-member confirmations than `num_confirmations` requires, breaking the core custody guarantee of the multisig: `count(confirmations from members ∈ current members) ≥ num_confirmations` before any `Transfer`/`FunctionCall`/`AddKey` action is executed.

### Finding Description
`confirm()` reads the stored confirmation set for a request and compares its size against `self.num_confirmations`: [1](#0-0) 

The set is only ever populated with `member.to_string()` at the time of confirming, and nothing revalidates that those entries are still valid members when the threshold check runs.

`delete_member` removes a departing member from `self.members` and clears requests/confirmations, but the cleanup is scoped only to requests whose *creator* (`r.member`) equals the removed member: [2](#0-1) 

It does **not** scan `self.confirmations` for entries where the removed member appears as a *confirmer* on requests created by someone else. Those stale confirmation strings remain in the `HashSet<String>` for those other, still-pending requests.

`assert_valid_request`, called from both `confirm` and `delete_request`, only verifies that the *caller* is currently a member — it never re-checks the *stored* confirmations set for staleness: [3](#0-2) 

Consequently, a confirmation cast by a member who is later removed keeps counting toward the threshold for any request they weren't the creator of.

### Impact Explanation
This breaks the binding "requests execute only once genuinely live members supply `num_confirmations` approvals." A request can reach and cross the threshold using one or more confirmations from accounts that are no longer authorized multisig members, so a `Transfer`, `AddKey`, or `FunctionCall` action can be executed with fewer real approvals than the configured threshold — i.e., NEAR can be moved, or a full-access key added, without the intended number of currently-authorized signers agreeing. Per the impact classification, this is a **Critical** issue: "a multisig request executed below threshold."

### Likelihood Explanation
No privileged actor beyond ordinary multisig governance is required: any member can create and confirm requests, and member removal is a routine `DeleteMember` action available to the multisig's own threshold. The only precondition is that a member who confirmed a request created by someone else is later removed before that request accumulates the remaining confirmations — a realistic offboarding scenario (e.g., revoking a departing employee's/partner's key) that legitimate multisig operators would perform without realizing it partially "banks" that member's stale vote on unrelated pending requests.

### Proof of Concept
1. Initialize `MultiSigContract::new` with members `{A, B, C, M}` and `num_confirmations = 2`.
2. Member `B` calls `add_request` with a `Transfer` action to an address `B` controls (request `R`, creator = `B`).
3. Member `M` calls `confirm(R)`. `confirmations[R] = {M}` (`1 < 2`, not yet executed) — see `confirm` at [4](#0-3) .
4. The multisig, via a separate legitimate request, executes `DeleteMember { member: M }`. `delete_member` only removes requests where `r.member == M` (i.e., requests *M* created); `R` (created by `B`) is untouched, so `confirmations[R]` still equals `{M}` even though `M` is no longer in `self.members` — see [5](#0-4) .
5. Current membership is now `{A, B, C}`. Member `B` calls `confirm(R)` again. `confirmations[R].len() == 1` (stale `M`) `+ 1 == 2 >= num_confirmations`, so `execute_request` runs the `Transfer` — see the threshold check at [6](#0-5) .
6. The transfer executes with only one confirmation (`B`) from a currently live member plus one stale confirmation from removed member `M`, i.e., below the intended 2-live-member threshold.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` (not just requests created by the removed member) and strip the removed member's confirmation string from every set; if this drops any request's confirmation count logic, re-persist the pruned set. Alternatively, at `confirm`/`execute_request` time, filter the stored confirmation set to only those members present in `self.members` before comparing against `num_confirmations`.

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
