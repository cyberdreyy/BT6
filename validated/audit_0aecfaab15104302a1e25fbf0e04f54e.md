## Title
Stale Confirmations From Deleted Multisig Members Are Still Counted Toward the Confirmation Threshold - (File: `multisig2/src/lib.rs`)

## Summary
`MultiSigContract::delete_member()` in `multisig2/src/lib.rs` only removes pending requests that the *departing* member itself created, but never scrubs that member's prior *confirmations* recorded on other members' requests. Because `confirm()` counts `confirmations.len()` (a raw string set) against `self.num_confirmations` without verifying every entry still belongs to a current member, a request can be executed after fewer live members actually approved it than the configured K-of-N threshold requires — breaking the binding "confirmations counted == confirmations from current live members."

## Finding Description
`confirm()` accepts a `request_id`, looks up the `HashSet<String>` of confirmations for it, and executes the request once `confirmations.len() as u32 + 1 >= self.num_confirmations`: [1](#0-0) 

The only membership check performed at confirm time is on the *current caller* (`self.current_member()`); the existing entries already stored in the `confirmations` set are never re-validated against the current `self.members` set.

`delete_member()` is the only place that prunes stale state when a member is removed, but it filters requests by `r.member == member` — i.e. only requests that the removed member *created* (the `MultiSigRequestWithSigner.member` field) are deleted: [2](#0-1) 

It does **not** iterate `self.confirmations` to strip the removed member's `to_string()` entry from requests they merely *confirmed* (but did not create). Consequently, if member `A` confirms a request created by member `B`, and `A` is later removed via `DeleteMember`, the confirmation string for `A` remains inside that request's `confirmations` HashSet forever, and still counts toward `self.num_confirmations` in the `>=` comparison in `confirm()`.

## Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." A K-of-N multisig can be made to execute a `Transfer`, `FunctionCall`, `AddKey`, etc. action with fewer than K *currently authorized* members having approved it, because one or more of the counted confirmations come from an account/key that has since been removed from `self.members` (e.g., because it was compromised, off-boarded, or intentionally revoked). This defeats the core security guarantee of the multisig contract described in its own README ("Any of the access keys or set of specified accounts can confirm, until the required number of confirmation achieved").

## Likelihood Explanation
No privileged action or redeploy is required beyond the multisig's documented, ordinary lifecycle: members regularly get added/removed (`AddMember`/`DeleteMember` are first-class supported actions), and members confirming requests created by other members is the normal, encouraged workflow. Any deployment that removes a member after that member confirmed at least one still-pending request is silently exposed; no special initialization or misconfiguration is needed.

## Recommendation
When removing a member in `delete_member()`, iterate over `self.confirmations` (not just `self.requests` filtered by creator) and remove the departing member's `to_string()` entry from every request's confirmation set, e.g.:
```rust
for (request_id, mut confirmations) in self.confirmations.iter() ... {
    if confirmations.remove(&member.to_string()) {
        self.confirmations.insert(&request_id, &confirmations);
    }
}
```
Alternatively (and more robustly), change `confirm()` to recompute the *live* confirmation count by filtering `confirmations` against `self.members.contains(...)` before comparing to `self.num_confirmations`, so stale entries can never be counted even if cleanup is missed elsewhere.

## Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. Member `B` calls `add_request(R1)` where `R1` is `Transfer { amount, receiver_id: attacker }`. `R1.confirmations = {}`.
3. Member `A` calls `confirm(R1)` → `R1.confirmations = {A}` (1 < 3, not executed).
4. Members legitimately remove `A` (e.g., key compromise) via a separate fully-confirmed `DeleteMember { member: A }` request. `delete_member()` only removes requests where `r.member == A` (i.e., requests A created); `R1` was created by `B`, so it is untouched, and `R1.confirmations` still contains `A`.
5. Now `self.members = {B, C, D}`, `self.num_confirmations = 3`.
6. Member `B` calls `confirm(R1)` → `confirmations = {A, B}`, len 2 (< 3, not yet executed).
7. Member `C` calls `confirm(R1)` → `confirmations = {A, B, C}`, len 3 → `3 >= 3` → `execute_request(R1)` fires, transferring funds to `attacker`.

Only `B` and `C` — 2 of the 3 currently required live members — ever authorized this transfer; `A`'s stale, revoked confirmation was counted, letting the request execute below the intended threshold.

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
