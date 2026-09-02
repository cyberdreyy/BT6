### Title
Stale confirmations from removed multisig members still count toward the execution threshold - (multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` decides whether a request has reached its execution threshold purely by counting the size of the stored `confirmations` `HashSet<String>` for that request. When a member is removed via `delete_member`, only requests it *originated* are purged; confirmations it previously cast on requests originated by *other* members are never removed. A request can therefore execute once `confirmations.len() + 1 >= num_confirmations` even though one or more of the counted confirmations belong to accounts/keys that are no longer members, i.e. the request is executed with fewer than `num_confirmations` live confirmations.

### Finding Description
`confirm()` reads the confirmation set for a request and checks only its cardinality against `num_confirmations`: [1](#0-0) 

`assert_valid_request()` verifies that the *caller* confirming right now is a current member, but never re-validates the members already recorded in the stored `confirmations` set: [2](#0-1) 

`delete_member()` is the only place that scrubs confirmation state, and it only removes requests where the removed member was the *submitter* (`r.member == member`), not requests it merely confirmed: [3](#0-2) 

Because of this, the invariant the protocol relies on — `live_confirmations(request) >= num_confirmations` before execution — can be violated as:

`stored_confirmations(request).len() >= num_confirmations` while `live_confirmations(request) < num_confirmations`, because `stored_confirmations` retains entries for members that were valid at the time of confirming but have since been removed by `DeleteMember`.

### Impact Explanation
This directly matches the "a multisig request executed below threshold" Critical impact category: a `Transfer`, `AddKey`, `FunctionCall`, or any other `MultiSigRequestAction` can be executed with fewer real, current-member confirmations than `num_confirmations` requires, because a stale confirmation from an already-removed member is still counted. This breaks the core custody binding of the multisig: `confirmations counted == confirmations from live members`.

### Likelihood Explanation
The sequence requires only ordinary governance activity, no malicious or privileged actor beyond the multisig's own normal operation:
1. Member `B` confirms request `X` (submitted by member `A`), leaving `confirmations = {B}` (below threshold, so it stays pending).
2. The multisig later executes a (fully legitimate, correctly-thresholded) `DeleteMember { member: B }` request, removing `B` from `self.members`. `delete_member` does not touch request `X`'s confirmation set because `X.member == A`, not `B`.
3. Member `C` (a live, valid member) confirms request `X`. `confirmations.len() (1, stale B) + 1 (C) = 2 >= num_confirmations (2)` → the request executes, even though only `C` is currently a real, live confirming member.

Any multisig where membership changes over time (a normal, expected lifecycle event) while requests remain pending is susceptible; no exploit of a trusted role is required, only ordinary sequencing of legitimate actions.

### Recommendation
When counting confirmations in `confirm()`, filter the stored `confirmations` set down to entries that are still `self.members.contains(...)` before comparing against `num_confirmations`, or proactively prune confirmations for a removed member across *all* pending requests (not just requests it submitted) inside `delete_member()`.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C}` and `num_confirmations = 2`.
2. `A.add_request(X)` — a `Transfer` request to some receiver.
3. `B.confirm(X)` — `confirmations[X] = {B}` (1 < 2, stays pending).
4. `A.add_request_and_confirm(DeleteMember{member: B})`, `C.confirm(...)` — executes, removing `B` from `members`; `X` is untouched because its submitter is `A`, not `B`.
5. `C.confirm(X)` — `confirmations[X].len() (1) + 1 = 2 >= num_confirmations (2)` → `X` executes, funded/authorized by only one genuinely current confirmer (`C`) plus one stale confirmation from a removed member (`B`), violating the intended `k`-of-`n` threshold guarantee. [1](#0-0) [3](#0-2)

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
