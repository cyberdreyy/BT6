## Analog Bug Found

### Title
Confirmations from a removed multisig member remain counted toward the execution threshold on other pending requests - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` decides whether a request is executed purely by comparing the *size* of the stored `confirmations` set for that request against `num_confirmations`. When a member is removed via `DeleteMember`, `delete_member` only purges confirmations/requests that the removed member itself *authored*, not confirmations the removed member previously cast on *other* members' pending requests. Those stale confirmations keep counting toward the threshold, so a request can execute with fewer than `num_confirmations` currently-live members having approved it — the same class of "recorded claim vs. live/actual state" mismatch as the reported `payoutPeriodStartTime` bug, here breaking the confirmations-counted-vs-live-members binding instead of a time-period binding.

### Finding Description
`confirm` only inspects the cardinality of the confirmations `HashSet`: [1](#0-0) 

`delete_member`, invoked when a `DeleteMember` request executes, removes member-authored requests and their confirmations, but does nothing to scrub confirmations the deleted member previously placed on requests authored by *other* members: [2](#0-1) 

Nothing in `confirm`, `assert_valid_request`, or `remove_request` re-validates that every account_id/public-key present in a request's `confirmations` set is still a current entry in `self.members` before counting it: [3](#0-2) 

So the binding the contract is supposed to preserve — `len(confirmations that are still live members) >= num_confirmations` before executing a request — silently degrades to `len(confirmations ever recorded, including stale/removed members) >= num_confirmations`.

### Impact Explanation
This crosses the exact authorisation-threshold boundary called out in scope: "a multisig request executed below threshold" (Critical impact). A member who confirmed a pending request and is later removed (e.g. because their key was compromised, or as routine key rotation) still has their confirmation "vote" honored forever on any request they touched before removal. An attacker or a malicious/compromised member can pre-confirm several sensitive pending requests (e.g. `Transfer`, `AddKey`) before being removed (or right before their removal is executed), and those confirmations remain valid credits toward the threshold even though the account no longer belongs to the K-of-N set — effectively letting a request execute with fewer genuinely-current approvers than `num_confirmations` requires.

### Likelihood Explanation
No special privilege beyond being a legitimate multisig member at some point is required: any member can confirm several outstanding requests before their own removal (which they can even trigger themselves as part of a routine key-rotation/off-boarding flow), and those confirmations are never invalidated afterward. Any request left pending across a `DeleteMember` execution is affected — this requires no attack complexity beyond ordinary operational sequencing (confirm request → get removed → the request is later completed by fewer additional live members than the policy specifies).

### Recommendation
When a member is deleted, iterate over **all** pending requests' confirmation sets (not just the ones the removed member authored) and strip out the removed member's identifier. Alternatively, when counting confirmations in `confirm`, filter the confirmations set against `self.members` (only count entries still present) before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with 5 members `{A,B,C,D,E}` and `num_confirmations = 3`.
2. `A` calls `add_request` for a sensitive `Transfer` request `R` (receiver = attacker-controlled account).
3. `B` calls `confirm(R)` → `confirmations(R) = {B}` (size 1, below threshold, no execution).
4. Separately, `A,C,D` confirm a `DeleteMember{member: B}` request (unrelated, reaches threshold 3) and it executes: `delete_member` removes `B` from `self.members`, but only cleans confirmations/requests where `request.member == B` — `R` was authored by `A`, so `confirmations(R) = {B}` is left untouched.
5. Now only `C` and `D` (2 more live members) need to call `confirm(R)`. After `C` and `D` confirm, `confirmations(R).len() + 1 = 3 >= num_confirmations`, and `R` executes — transferring funds — even though `B`'s vote is no longer a live member's vote, meaning only 2 currently-live members plus one stale, removed member's vote authorized a supposedly 3-of-N transfer. [1](#0-0) [2](#0-1)

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

**File:** multisig2/src/lib.rs (L381-404)
```rust
    /// Removes request, removes confirmations and reduces num_requests_pk - used in delete, delete_key, and confirm
    fn remove_request(&mut self, request_id: RequestId) -> MultiSigRequest {
        // remove confirmations for this request
        self.confirmations.remove(&request_id);
        // remove the original request
        let request_with_signer = self
            .requests
            .remove(&request_id)
            .unwrap_or_else(|| env::panic_str("Failed to remove existing element"));
        // decrement num_requests for original request signer
        let original_member = request_with_signer.member;
        let mut num_requests = self
            .num_requests_pk
            .get(&original_member.to_string())
            .unwrap_or(0);
        // safety check for underrun (unlikely since original_signer_pk must have num_requests_pk > 0)
        if num_requests > 0 {
            num_requests = num_requests - 1;
        }
        self.num_requests_pk
            .insert(&original_member.to_string(), &num_requests);
        // return request
        request_with_signer.request
    }
```
