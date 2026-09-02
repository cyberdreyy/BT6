## Finding

I found a concrete analog of the "stale cache not invalidated on authority change" bug class in the `multisig2` contract: **stale confirmations from a deleted member persist and count toward the confirmation threshold**, breaking the K-of-N custody guarantee.

### Title
Deleted multisig members' stale confirmations still count toward execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only purges confirmations for requests that the removed member itself *created*; it never scans other active requests to strip confirmations that the removed member had *contributed* to. `confirm` only checks the raw size of the stored confirmation set against `num_confirmations`, without verifying that every recorded confirmer is still a current member. As a result, a request can execute after being approved by fewer live members than `num_confirmations` requires.

### Finding Description
`confirm` ( [1](#0-0) ) executes a request once `confirmations.len() + 1 >= self.num_confirmations`, where `confirmations` is a plain `HashSet<String>` of member identities that previously called `confirm` on this `request_id`.

`delete_member` ( [2](#0-1) ) removes the member from `self.members`, deletes their access key, and cleans up *only the requests they themselves created* (`r.member == member`), along with those requests' confirmation sets. It does not iterate over `self.confirmations` for requests created by *other* members to remove entries left behind by the deleted member.

Consequently, once a member is removed, any confirmation they previously placed on a request created by someone else remains counted in `confirmations.len()`, silently satisfying part of the threshold with a party that is no longer authorized.

The correct invariant should be: `count(confirmations still belonging to current members) == confirmations.len()`. After a `DeleteMember`, this equality is broken because a stale entry from a non-member is retained.

### Impact Explanation
This is Critical: it allows **a multisig request to be executed below the intended threshold** — the exact impact category called out for multisig custody bindings. A transfer, key addition, or arbitrary function call can be pushed through with confirmations from fewer than `num_confirmations` *currently valid* members, since one "confirmation" belongs to someone whose authority was already revoked. This directly moves NEAR (or grants access) that the remaining live members did not fully authorize.

### Likelihood Explanation
No privileged actor or malicious node is required — this is triggerable purely through the multisig's own documented request/confirm/delete-member flow by existing members acting in the normal order the contract allows (create a request, get partial confirmations, then separately vote to remove one of the confirmers, then get the last live confirmation). No deployment misconfiguration or off-chain assumption is needed.

### Recommendation
When executing `DeleteMember`, iterate all outstanding `confirmations` entries (not just requests created by the removed member) and remove the deleted member's entry from each. Alternatively, validate each confirmer's continued membership at `confirm`-time before counting toward the threshold (e.g., filter `confirmations` against `self.members` before comparing to `num_confirmations`).

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm` on request `R` (e.g., `Transfer`) → `confirmations(R) = {A}`.
3. `B` calls `confirm(R)` → `confirmations(R) = {A, B}` (2/3, not yet executed). [1](#0-0) 
4. Separately, `A`, `C`, `D` create and confirm request `R2 = DeleteMember{B}` (3/3), which executes `delete_member` for `B`. Membership check (`self.members.len() - 1 >= num_confirmations`) passes (3 members remain), and `B`'s confirmation on `R` is untouched because `R` was not created by `B`. [2](#0-1) 
5. `C` calls `confirm(R)`. `confirmations(R).len() == 2` (still contains stale `B`) `+ 1 == 3 >= num_confirmations`, so `R` executes. [3](#0-2) 

Result: `R` (e.g., a `Transfer`) executes with confirmations from only `A` and `C` — two currently-live members — plus one stale confirmation from `B`, who was removed from the multisig before the request executed. The 3-of-4 (now 3-of-3) threshold was bypassed by a non-member's leftover approval.

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
