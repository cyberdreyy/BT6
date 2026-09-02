### Title
Confirmations from removed multisig members remain counted toward the execution threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` tallies the confirmations recorded in `self.confirmations` for a request without re-validating that each previously-recorded confirmer is still a current member. When a member is deleted via `DeleteMember`, `delete_member` only purges requests/confirmations for which the *removed* member was the original requester, not confirmations the removed member cast on other members' pending requests. As a result, a request can execute once the stored confirmation count reaches `num_confirmations`, even though one or more of those confirmations came from an account/key that is no longer a multisig member, effectively executing the request below the true live-member threshold.

### Finding Description
`confirm` checks only that the *caller* is a current member via `current_member()`/`assert_valid_request`, then compares the size of the stored confirmation set against `num_confirmations`: [1](#0-0) 

Membership removal is handled in `delete_member`, which cleans up confirmations only for requests whose *creator* (`r.member`) equals the removed member: [2](#0-1) 

Note the filter is `r.member == member` (the request's original signer), not a scan of every request's confirmation set for entries belonging to the removed member. Confirmations that a soon-to-be-removed member cast on requests created by *other* members are never purged. `assert_valid_request` (used by both `confirm` and `delete_request`) likewise never re-checks the validity of stored confirmers: [3](#0-2) 

Consequently the equality the contract is supposed to maintain — `confirmations counted == confirmations from currently-live members` — can be broken: a stale confirmation from a departed member persists in the set and is counted toward `num_confirmations` when a later, still-valid member pushes the tally over the threshold.

### Impact Explanation
This crosses an authorization/threshold boundary explicitly called out as Critical: "a multisig request executed below threshold." A request can be executed with fewer genuinely live, currently-authorized confirmations than `num_confirmations` requires, undermining the K-of-N security guarantee the multisig is designed to provide (e.g., a stale confirmation could let a minority of current members push through a `Transfer`, `AddKey`, `FunctionCall`, or another `DeleteMember`/`AddMember` action).

### Likelihood Explanation
This requires no attacker-controlled key theft, no victim key, and no owner/foundation misbehavior beyond the multisig's own normal, documented operation: adding a request, having some members confirm it, and then legitimately removing one of those confirming members through a separate, properly-threshold-approved `DeleteMember` request before the first request reaches quorum. This is a plausible operational sequence for any long-lived multisig that rotates members (a common practice), so likelihood is not purely theoretical.

### Recommendation
When executing `DeleteMember` (and the analogous `DeleteKey`/removal logic in `multisig/src/lib.rs` v1), iterate over **all** pending requests' confirmation sets (not just requests created by the removed member) and strip the removed member's entry from each. Alternatively, at `confirm`/tally time, filter the stored confirmation set to only those confirmers who are still `self.members.contains(..)` before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R)` for some `Transfer`/`FunctionCall` request → `confirmations[R] = {A}`.
3. `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (2 < 3, not yet executed).
4. Separately, `A`, `C`, `D` create and confirm a `DeleteMember { member: B }` request, reaching the 3-confirmation threshold and executing it. `delete_member` filters `self.requests` for requests created by `B` — `R` was created by `A`, so its confirmation set is untouched; `B` remains in `confirmations[R]`, and `B` is now removed from `self.members`.
5. `C` calls `confirm(R)`. In `confirm`, `confirmations.len()` is `2` (`A`, `B`), so `2 + 1 >= 3` is true, and `R` executes — even though the live-member confirmations behind it are only `A` and `C` (2 of the 4 remaining members), one short of the intended 3-of-N threshold.

This demonstrates a request executing with a stale, no-longer-valid confirmation counted toward the threshold, breaking the confirmations-vs-live-members equality without any attacker keys, redeploys, or foundation/owner intervention beyond the multisig's normal member-management flow.

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

**File:** multisig2/src/lib.rs (L356-379)
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
