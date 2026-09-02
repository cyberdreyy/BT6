### Title
Stale confirmations from removed multisig members still count toward the confirmation threshold, allowing requests to execute with fewer live confirmations than required - (multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` only purges requests that were *originated* by the removed member; it never scans the `confirmations` map to strip that member's confirmation from requests originated by *other* members. `confirm()` then compares `confirmations.len() + 1` against `num_confirmations` without checking that every entry in the confirmation set still corresponds to a current member of `self.members`. A member who confirmed a request and is later removed from the multisig therefore continues to count toward quorum for that request, letting the remaining live members execute it with fewer than `num_confirmations` currently-authorized signers.

### Finding Description
The multisig's core invariant is: *a request executes only when at least `num_confirmations` distinct **current** members have approved it.* This binding is broken because confirmation records are keyed by a stringified `MultisigMember` and never invalidated on membership removal for requests they did not submit.

- `confirm()` reads the existing confirmation set and executes once `confirmations.len() as u32 + 1 >= self.num_confirmations`, with no cross-check against `self.members`: [1](#0-0) 

- `delete_member()` cleans up `requests`/`confirmations` only for requests where `r.member == member`, i.e. requests the removed member **submitted**. It removes the member from `self.members` and revokes their access key, but does nothing about confirmation entries they left on requests submitted by other members: [2](#0-1) 

- `assert_valid_request()` (called by both `confirm` and `delete_request`) only checks that the *caller* is currently a member; it never re-validates the already-stored confirmation set against current membership: [3](#0-2) 

So a confirmation granted before a member is removed remains a permanent, un-revocable "vote" on any request it was attached to, effectively letting a de-authorized identity keep contributing to the K-of-N quorum — the exact class of bug described in the report ("confirmations counted versus live members"): the authentication/trust check does not verify the counted party is still a currently-trusted principal.

### Impact Explanation
This lets a request be executed with fewer live, currently-authorized members than `num_confirmations` mandates — i.e. a multisig request executed below the threshold. Since `MultiSigRequestAction` includes `Transfer`, `AddKey`, `FunctionCall`, `DeployContract`, etc., this can be used to move funds, add unauthorized access keys, or redeploy the contract using an effective quorum smaller than intended. This matches the "Critical" impact class explicitly called out in scope: "a multisig request executed below threshold."

### Likelihood Explanation
This requires no privileged foundation action, no redeploy, and no key compromise beyond the normal multisig workflow: it can be triggered whenever a member who previously confirmed some pending request is later removed (a routine, expected multisig-governance action, e.g. rotating out a compromised or departing member) while that request is still outstanding. Any member subsequently completing confirmation on that stale request benefits from the "free" leftover confirmation. This is a straightforward, unprivileged-attacker-reachable path through ordinary contract usage, not a theoretical edge case.

### Recommendation
When removing a member in `delete_member`, iterate all `requests`/`confirmations` and strip the removed member's entry from every confirmation set (not just requests they authored), or alternatively validate at `confirm()` time that every entry in a request's confirmation set still belongs to `self.members`, discounting stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C], num_confirmations = 2)`.
2. Member `B` calls `add_request(Transfer{...})` → request `R` created, `confirmations[R] = {}`.
3. Member `A` calls `confirm(R)` → `confirmations[R] = {A}` (only 1 of 2 needed, so it doesn't execute yet). [4](#0-3) 
4. Members `B` and `C` submit and confirm a `DeleteMember{member: A}` request (2 confirmations reached), removing `A` from `self.members` and revoking its key. Note `delete_member` only cleans requests where `r.member == A` (requests A submitted); `R` was submitted by `B`, so it is untouched and still holds `A`'s stale confirmation: [5](#0-4) 
5. Member `C` now calls `confirm(R)`. `confirmations[R].len()` is `1` (stale `A`) `+ 1` (`C`) `= 2 >= num_confirmations (2)`, so `execute_request` runs the `Transfer`, even though only `C` is a currently live, authorized confirmer alongside a revoked member `A`. [6](#0-5)

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

**File:** multisig2/src/lib.rs (L406-421)
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
```
