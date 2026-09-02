### Title
Stale confirmations from removed multisig members are still counted toward `num_confirmations`, allowing a request to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges the outstanding *requests that were originated by* the member being removed, but never removes that member's confirmation entries recorded on *other, still-pending requests*. Because `confirm` counts entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set without checking whether each entry still belongs to a current member of `self.members`, a request can be executed even though the number of confirmations from *live* members is below `num_confirmations`.

### Finding Description
The confirmation-counting binding that must hold is:
`confirmations_counted(request_id) == confirmations_from_live_members(request_id)`

`confirm()` checks this invariant implicitly by comparing set size to `num_confirmations`: [1](#0-0) 

`delete_member()` is the only place that removes a member and it explicitly filters requests by `r.member == member` — i.e. requests *originated* by the departing member — but does nothing to scan and strip that member's key/account from the `confirmations` HashSet of any other request: [2](#0-1) 

Contrast this with `remove_request`, which is only invoked for requests being fully removed (deleted or executed), and never iterates all pending requests to clean stale confirmer identities either: [3](#0-2) 

As a result, once a member who has already confirmed some other pending request is removed via a legitimate `DeleteMember` action, that stale confirmation entry remains in the set and is later counted by `confirm()` toward `num_confirmations`, even though that account/key is no longer a member and cannot itself call `confirm` (its access key was deleted / its account no longer passes `current_member()`).

### Impact Explanation
This breaks the confirmations-counted-versus-live-members custody binding directly named in scope. A pending `Transfer`, `AddKey`, `FunctionCall`, or `DeployContract` request can be pushed to execution with fewer *live* member confirmations than `num_confirmations` requires, because one "confirmation" slot is filled by an account/key that has since been removed from the multisig. This is a Critical-class impact per the stated rules: "a multisig request executed below threshold."

### Likelihood Explanation
The scenario requires no attacker privilege beyond normal multisig operation: any member can confirm a pending request, and any subsequent (legitimate) `DeleteMember` action removing that member does not clean up the stale confirmation. This is a natural sequence of ordinary operations (member confirms a request, then leaves/is removed from the multisig for unrelated reasons) rather than a contrived edge case, making it readily reachable.

### Recommendation
When removing a member in `delete_member`, iterate all pending `requests`/`confirmations` (not just the ones authored by that member) and strip the departing member's identity string from every confirmation `HashSet`. Alternatively, when counting confirmations in `confirm()`, filter the confirmation set down to only entries that are still `self.members.contains(...)` before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `A` calls `add_request(X)` (a `Transfer` request); `X.member == A`, confirmations for `X` = `{}`.
3. `B` calls `confirm(X)` → confirmations for `X` = `{B}` (1/3, `len()+1 = 1 < 3`, no execution) — see `confirm` logic: [4](#0-3) .
4. `C` calls `confirm(X)` → confirmations for `X` = `{B, C}` (2/3, `len()+1 = 2 < 3`, no execution).
5. Separately, members legitimately vote (via a different multisig request requiring 3/4 confirmations from `A, C, D`) to `DeleteMember { member: B }`. `delete_member` asserts `members.len() - 1 (=3) >= num_confirmations (=3)`, passes, and removes `B` — but only scrubs requests where `r.member == B` (none, since `X.member == A`), leaving `B`'s confirmation on `X` untouched: [5](#0-4) .
6. Members are now `{A, C, D}`. `D` calls `confirm(X)`: `confirmations.len() (=2, i.e. {B, C}) + 1 = 3 >= num_confirmations (3)` → `execute_request(X)` fires the `Transfer`, even though only 2 *live* members (`C` and `D`) ever actually confirmed it, below the intended 3-of-4 (now 3-of-3) threshold.

Unknown/uncertain: I was not able to fully trace the equivalent v1 `multisig/src/lib.rs` (`delete_key`) path in this session to confirm whether the same stale-confirmation gap exists there, since its member-removal helper was not read in full before the iteration budget ended.

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
