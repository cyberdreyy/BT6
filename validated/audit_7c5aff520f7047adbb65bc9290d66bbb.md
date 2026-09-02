I have sufficient evidence to write up the finding. The `multisig2` contract's `delete_member` only purges confirmations for requests **created** by the removed member, but not confirmations the removed member gave as a *voter* on other still-pending requests, and `confirm()` never re-validates that already-recorded confirmers are still current members.

### Title
Stale confirmations from removed members still count toward the confirmation threshold, allowing requests to execute below the effective live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts the size of the `confirmations: LookupMap<RequestId, HashSet<String>>` set for a request and executes it once the size reaches `num_confirmations`, without checking that every entry in that set is still a current member of `self.members`.

### Finding Description
When a member confirms a request via `confirm`, their identity (`member.to_string()`) is inserted into the `confirmations` set for that `request_id`: [1](#0-0) 

If that member is later removed from the multisig via a `DeleteMember` request, `delete_member` only cleans up requests that the removed member itself **created** (`r.member == member`), and it removes `num_requests_pk` bookkeeping for that member — but it does **not** scan or purge the member's entries from `confirmations` sets belonging to other, still-pending requests where the member only voted (confirmed) rather than created: [2](#0-1) 

`assert_valid_request`, called at the top of `confirm`, only checks that the *caller* is currently a member — it never re-validates the members already present in the stored `confirmations` set: [3](#0-2) 

As a result, the binding "confirmations counted == confirmations from currently live members" is broken: `confirmations.len() >= num_confirmations` can be satisfied by a mix of live and already-removed members.

### Impact Explanation
This lets a `MultiSigRequest` (e.g., a `Transfer` of NEAR, `FunctionCall`, or `AddKey`) execute with fewer *currently authorized* confirmations than `num_confirmations` actually requires, because a stale vote from a member who has since been removed (e.g., for being compromised or off-boarded) still counts. This is a multisig request executed below its intended threshold, matching the Critical impact category for a threshold bypass.

### Likelihood Explanation
This requires no attacker-controlled deployment or privileged foundation action — it only requires normal multisig operation: a member votes on a pending request, membership changes later (a routine `DeleteMember` action, which is a documented, expected multisig operation), and the original pending request is subsequently confirmed by fewer live members than the policy requires. No cooperation from the removed member is needed after their initial (potentially legitimate, contemporaneous) confirmation.

### Recommendation
When executing `delete_member`, iterate all entries in `self.confirmations` (not just requests created by the removed member) and strip the removed member's entry from every confirmation set; alternatively, when checking `confirmations.len() as u32 + 1 >= self.num_confirmations` in `confirm`, filter/recompute the count based only on entries whose member is still present in `self.members`.

### Proof of Concept
1. Multisig deployed with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` for `Transfer{amount}` to an attacker-controlled account, `request_id = R`.
3. `A` confirms via `confirm(R)` → `confirmations[R] = {A}`.
4. `B` confirms via `confirm(R)` → `confirmations[R] = {A, B}` (2 of 3 needed).
5. Separately, members legitimately vote to remove `A` (e.g., key compromise) via a `DeleteMember{A}` request, which reaches threshold and executes `delete_member`. This only purges requests **created** by `A`; `R` (created by... assume created by someone else, or even if created by `A`, consider a second pending request `R2` created by `C` where `A` had confirmed) — `confirmations[R2]` still contains `A`.
6. Only one more live member (`C` or `D`) needs to confirm `R2` to reach `3`, even though only 2 *live* members (the new confirmer plus the pre-existing live confirmer, excluding removed `A`) actually authorized it — bypassing the intended 3-of-4-live-members policy.

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
