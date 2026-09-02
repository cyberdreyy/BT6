### Title
Stale confirmations from removed multisig members are still counted toward the execution threshold, allowing a request to execute below the configured `num_confirmations` - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests and confirmation sets for requests that the *removed* member itself created, but never scrubs that member's confirmation entry from the confirmation sets of *other* still-pending requests they merely co-signed. `confirm()` later counts confirmations purely by `HashSet::len()`, without re-validating that every entry still corresponds to a current member. This lets a request execute with fewer live, authorized confirmations than `num_confirmations` requires.

### Finding Description
The confirmation-counting invariant should be:
`(number of confirmations from currently authorized members for request R) >= num_confirmations`

Instead the code checks:
`confirmations.get(request_id).len() >= num_confirmations` [1](#0-0) 

where `confirmations` is a raw `HashSet<String>` of member identifiers collected over time, with no re-validation against the live `members` set at confirm-time [2](#0-1) .

When a member is deleted via `delete_member`, the cleanup only removes requests/confirmations *created by* that member (`r.member == member`); it does not scan and strip that member's string from the `confirmations` HashSet of requests created by other members that this member had already confirmed: [3](#0-2) 

The `assert_valid_request` helper called from `confirm` only checks that the *caller* is currently a member; it does not re-validate the historical confirmers already recorded in the set: [4](#0-3) 

This is directly analogous to the `isSafeLead` bug class: a cached/stale authorization artifact (a recorded confirmation, like a cached `lead` pointer) is trusted without checking whether the underlying role/membership has since been revoked.

### Impact Explanation
This breaks the multisig's core custody guarantee: the number of confirmations required to move funds or change contract state is meant to always be satisfied by currently-authorized members. With this bug, a stale confirmation from a member removed via `DeleteMember` continues to count, so a request (e.g. a `Transfer` action moving NEAR out of the multisig) can be pushed to execution with fewer genuinely live confirmations than `num_confirmations`, i.e. "a multisig request executed below threshold" — a Critical impact per the specified severity classes.

### Likelihood Explanation
This requires no privileged access beyond being (or having been) a member — it is a normal governance sequence:
1. A pending request already has some confirmations from a member who is later removed.
2. That member is removed via a separate, legitimate `DeleteMember` execution.
3. Any remaining member confirms the still-pending request; the stale confirmation is still counted, tipping the total over `num_confirmations` even though the count of genuinely live confirmers is lower.

No malicious insider action needed beyond normal request/confirm ordering — an attacker/member simply needs to leave a confirmation on a request before being removed, or times a removal against a pending request.

### Recommendation
When counting confirmations in `confirm()`, filter the stored confirmation identifiers against the current `members` set (or eagerly purge confirmations from all pending requests, not only requests created by the removed member, inside `delete_member`) before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R)` — `R.confirmations = {A}` (1/3).
3. `B` calls `confirm(R)` — `R.confirmations = {A, B}` (2/3, not yet executed).
4. Members legitimately execute a separate `DeleteMember { member: B }` request (reaching the required 3 confirmations from other members). `delete_member` removes `B` from `members`, but since `B` did not create `R`, `R`'s confirmation set `{A, B}` is left untouched.
5. `C` calls `confirm(R)`: `confirmations.len() + 1 == 3 >= num_confirmations (3)` → `execute_request(R)` runs, even though only `A` and `C` are actually current, authorized confirmers (2 of 3 required live members), because `B`'s stale confirmation was still counted. [1](#0-0) [5](#0-4)

### Citations

**File:** multisig2/src/lib.rs (L126-132)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
```

**File:** multisig2/src/lib.rs (L294-315)
```rust
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
