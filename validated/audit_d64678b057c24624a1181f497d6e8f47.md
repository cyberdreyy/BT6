Confirmed: `delete_member` at multisig2/src/lib.rs:355-379 only strips requests where `r.member == member` (requests originally *submitted* by the departing member) and never scrubs that member's key/account out of the `confirmations` HashSets stored for *other* still-open requests. `assert_valid_request` (lines 406-423) and `confirm` (lines 292-315) only check that the request/confirmations entries exist — they never re-validate that every string in `confirmations.get(&request_id)` still corresponds to a current `self.members` entry. So a confirmation cast while an account/key was a legitimate member remains permanently counted toward `num_confirmations` even after that member is removed via `DeleteMember`, letting a request execute while the number of *live* member confirmations is strictly less than the configured threshold. The multisig v1 contract (`multisig/src/lib.rs` `DeleteKey` handling, lines 198-216) has the same gap: it only purges requests signed by the deleted key, not that key's confirmations recorded on other members' requests.

### Title
Stale confirmations from removed multisig members are never purged, allowing requests to execute below the live confirmation threshold - (File: multisig2/src/lib.rs, multisig/src/lib.rs)

### Summary
`delete_member` (multisig2) / the `DeleteKey` action (multisig v1) removes a departing member from `self.members` and deletes only the *requests that member submitted*, but it never removes that member's `confirm`ations recorded on requests submitted by *other* members. Those stale confirmations continue to count toward `num_confirmations`, so a request can reach and cross the execution threshold using votes from accounts/keys that are no longer members.

### Finding Description
`confirm` at multisig2/src/lib.rs:292-315 increments/checks `confirmations.len()` against `self.num_confirmations` and, once the threshold is met, calls `execute_request`. The set of "who is a live member" lives in `self.members` (an `UnorderedSet<MultisigMember>`), a completely separate piece of state from `self.confirmations: LookupMap<RequestId, HashSet<String>>`. [1](#0-0) 

`delete_member` only filters `self.requests` for entries where `r.member == member` (i.e. requests that member *added*) and clears those. It does nothing to scrub `member.to_string()` out of the `confirmations` sets belonging to requests added by other members that this member had previously confirmed.

`assert_valid_request` (multisig2/src/lib.rs:406-423) checks only that the caller is currently a member, that the request exists, and that a confirmations entry exists for it — it performs no reconciliation between the stored confirmer identities and current membership. `confirm` therefore treats a stale confirmation identically to a live one when computing `confirmations.len() as u32 + 1 >= self.num_confirmations`.

Binding broken: **confirmations counted (`confirmations.len()`) versus live members (`self.members`)** — the invariant that "every counted confirmation belongs to a current member" is not enforced or restored after membership changes, exactly analogous to `QVBaseStrategy` failing to reset `reviewsByStatus` after a status transition (a counter that should track only the current review cycle instead persists stale votes).

The equivalent gap exists in the legacy `multisig` contract's `DeleteKey` action [2](#0-1) , which purges only requests signed by the removed key, not that key's confirmations on other open requests.

### Impact Explanation
This crosses the "authorisation" boundary called out for Critical impact: "a multisig request executed below threshold." Concretely, if `num_confirmations = 3` and Member D confirmed Request X (added by Member A) before being removed, then after D's removal the multisig effectively only needs 2 more *live* confirmations to execute X — the contract still believes it has 3 valid confirmations. Since `execute_request` can perform `Transfer`, `AddKey`/`DeleteKey`/`AddMember`/`DeleteMember`, `FunctionCall`, etc. on behalf of the multisig account, this can move NEAR or change control of the account with fewer live-member approvals than the multisig's own configured security threshold, undermining the entire K-of-N custody guarantee.

### Likelihood Explanation
This requires no external attacker privilege beyond normal, expected multisig operation: any account/key that once was a member and confirmed a still-pending request, and is later removed (a routine `DeleteMember`/`DeleteKey` maintenance action, e.g. key rotation or offboarding a member), leaves an exploitable stale confirmation. Any request that was open (added but not yet fully confirmed) at the time of removal is affected. Given that active requests can persist up to 15 minutes cooldown and up to `active_requests_limit` per member, the window is realistic in normal operational flows, not a contrived edge case.

### Recommendation
When removing a member (`delete_member` / `DeleteKey`), iterate over all outstanding requests' confirmation sets and remove the departing member's identity string from each, e.g.:
```rust
for (request_id, _) in self.requests.iter() {
    if let Some(mut confirmations) = self.confirmations.get(&request_id) {
        if confirmations.remove(&member.to_string()) {
            self.confirmations.insert(&request_id, &confirmations);
        }
    }
}
```
Alternatively, validate in `confirm`/`assert_valid_request` that every entry in `confirmations.get(&request_id)` still belongs to `self.members`, filtering out stale entries (and re-deriving the effective count) before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. Member A calls `add_request` to add a `Transfer` request `X` (receiver arbitrary), giving request id `rid`.
3. Member D calls `confirm(rid)` → `confirmations[rid] = {D}` (1/3).
4. Separately, members A, B, C submit and confirm a `DeleteMember { member: D }` request that reaches threshold and executes via `delete_member` (lines 355-379) — this removes D from `self.members`, removes only requests *D added*, but leaves `confirmations[rid] = {D}` untouched.
5. Member B calls `confirm(rid)` → `confirmations[rid] = {D, B}` (2/3, counting D even though D is no longer a member).
6. Member C calls `confirm(rid)` → `confirmations.len() + 1 = 3 >= num_confirmations(3)` → `execute_request` runs the `Transfer`.
7. Result: request `X` executed with only 2 live-member confirmations (B, C) instead of the required 3, because D's stale confirmation was still counted — violating the multisig's K-of-N custody guarantee. [3](#0-2) [4](#0-3)

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

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```
