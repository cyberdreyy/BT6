## Finding [1](#0-0) [2](#0-1) 

The external report's bug class — a value that is validated/relied upon at one point but never re-validated against present reality, causing custody logic to act on stale state — maps onto a real defect in `multisig2/src/lib.rs`: confirmations recorded for a request are never invalidated when the confirming member is later removed from the multisig.

### Title
Stale confirmations from removed members allow a MultiSigRequest to execute below the real live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` counts entries in a per-request `confirmations` `HashSet<String>` against `self.num_confirmations` without checking that every recorded confirmer is still a current member of `self.members`. `delete_member` only purges requests *authored* by the removed member (`r.member == member`), never confirmations that member cast on *other* still-open requests. This lets a request reach `num_confirmations` and execute even though fewer than `num_confirmations` currently-valid members actually approved it.

### Finding Description
`add_request`/`add_request_and_confirm` create a `MultiSigRequestWithSigner` and an empty `confirmations` set keyed by `request_id` [3](#0-2) . Any current member can call `confirm`, which inserts `member.to_string()` into that set and, once `confirmations.len() + 1 >= self.num_confirmations`, executes the request [2](#0-1) .

`delete_member` is the only place that mutates `self.members` for removal, and it also removes outstanding requests, but strictly filtered to `r.member == member`, i.e. only requests the removed member itself created:
```
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
``` [1](#0-0) 

Any request that member `M` merely *confirmed* (but did not author) keeps `M`'s entry in its `confirmations` set forever, even after `M` is deleted from `self.members`. `confirm()`'s threshold check (`confirmations.len() as u32 + 1 >= self.num_confirmations`) and `assert_valid_request` never cross-reference `self.members` against the stored confirmer strings [4](#0-3) . So the binding that should hold — `confirmations counted == confirmations from currently-live members` — is broken: `confirmations counted ⊇ confirmations from live members`, and a request can be pushed over threshold using a mix of live and stale (removed-member) confirmations.

### Impact Explanation
This directly matches the "Critical" impact category: a multisig request executed below the real (live-member) threshold. Concretely, with `num_confirmations = 3` and members `{A,B,C,D}`:
1. `A` creates and confirms transfer request `R` (`add_request_and_confirm`) → confirmations = `{A}`.
2. `B` confirms `R` → confirmations = `{A,B}` (still below 3, request stays open).
3. Members legitimately execute a separate, properly-threshold-approved request to remove `B` via `DeleteMember` — `delete_member` only clears requests `B` authored, not `R`, so `R`'s confirmations remain `{A,B}` even though `B` is no longer in `self.members`.
4. `C` (a live member) confirms `R` → `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `R` executes, transferring NEAR out of the multisig account, backed by only two genuinely current members (`A`,`C`) plus one stale, no-longer-valid confirmation (`B`).

This lets NEAR move out of the multisig custody account with less real authorization than the configured `num_confirmations` threshold guarantees — an accounting/authorization divergence that funds a transfer no longer backed by the required number of live approvers.

### Likelihood Explanation
This requires no attacker with special privileges beyond being an ordinary multisig member (which is inherent to how any multisig operates) and no foundation/owner/redeploy/social-engineering steps. It only requires: an open, partially-confirmed request; the normal, expected removal of a member (turnover is a documented, supported operation via `DeleteMember`); and one more live confirmation. Given that member rotation is an expected multisig lifecycle event, and requests can remain open indefinitely with no expiry tied to membership changes, this is readily triggerable by normal contract usage/timing rather than a contrived edge case.

### Recommendation
When executing a request in `confirm()` (and ideally also in `delete_member`), filter `confirmations` to intersect only with `self.members` before comparing against `num_confirmations`, e.g. re-validate that each stored confirmer string still corresponds to a current `MultisigMember` before counting it toward the threshold. Alternatively, on `delete_member`, scan all open requests' confirmation sets (not just those authored by the removed member) and strip the removed member's confirmation from each.

### Proof of Concept
1. Deploy `multisig2` with members `{A,B,C,D}` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm` with a `Transfer` request `R` to some receiver → confirmations(`R`) = `{A}`.
3. `B` calls `confirm(R)` → confirmations(`R`) = `{A,B}`.
4. Get 3 live members to approve and execute a `DeleteMember { member: B }` request (a normal, supported multisig operation) → `B` is removed from `self.members`; `R`'s confirmations set is untouched because `R` was authored by `A`, not `B` (see filter in `delete_member`, `multisig2/src/lib.rs:362-366`).
5. `C` calls `confirm(R)` → `confirmations.len() (2) + 1 == num_confirmations (3)` → `execute_request` runs and transfers funds, even though only `A` and `C` are actually live members who approved; `B`'s stale confirmation was counted toward the threshold.

### Citations

**File:** multisig2/src/lib.rs (L169-207)
```rust
    /// Add request for multisig.
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        let current_member = self.current_member().unwrap_or_else(|| {
            env::panic_str(
                "Predecessor must be a member or transaction signed with key of given account",
            )
        });
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&current_member.to_string())
            .unwrap_or(0)
            + 1;
        assert(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some.",
        );
        self.num_requests_pk
            .insert(&current_member.to_string(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
    }

    /// Add request for multisig and confirm with the pk that added.
    pub fn add_request_and_confirm(&mut self, request: MultiSigRequest) -> RequestId {
        let request_id = self.add_request(request);
        self.confirm(request_id);
        request_id
    }
```

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
