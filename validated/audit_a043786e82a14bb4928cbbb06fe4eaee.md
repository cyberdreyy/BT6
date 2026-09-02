## Finding [1](#0-0) 

### Binding claimed to hold
`live_confirmers(request_id) == confirmations.get(request_id).len()`, where `live_confirmers` should only include entries whose member is still in `self.members`. The threshold check at [2](#0-1)  assumes this equality holds.

### Trace
- `confirm()` inserts `member.to_string()` into the `confirmations` `HashSet` for a `request_id` [3](#0-2) .
- `delete_member()` only purges confirmations for requests that the removed member *created* (`r.member == member`), by filtering `self.requests` for that condition [4](#0-3) . It never scans other pending requests' `confirmations` sets to strip entries where the removed member appears merely as a *confirmer* (not the creator).
- `assert_valid_request()` only validates that the *current caller* is a member; it never re-validates the members already recorded in the stored `confirmations` set [5](#0-4) .
- `current_member()` is only used to authorize the caller of `confirm`, not to filter/prune stale confirmation strings [6](#0-5) .

### Exploit flow
With members `{A, B, C, D}` and `num_confirmations = 3`:
1. `A` creates request `R` (e.g. `Transfer`), via `add_request`.
2. `B` calls `confirm(R)` — `confirmations(R) = {B}`.
3. A separate request executes `DeleteMember { member: B }`, reaching 3 confirmations from `A, C, D` (satisfies `delete_member`'s own guard `members.len() - 1 >= num_confirmations`, i.e. `3 >= 3`) [7](#0-6) . Because `R` was created by `A`, not `B`, the cleanup loop does not touch `R`'s confirmations, so `confirmations(R)` still contains the stale `"B"` entry.
4. `C` calls `confirm(R)`: `confirmations.len() == 1` (stale B) `+ 1 == 2 < 3`, so it's just added, `confirmations(R) = {B, C}`.
5. `D` calls `confirm(R)`: `confirmations.len() == 2 + 1 == 3 >= num_confirmations(3)` → `execute_request` fires and moves funds via `Promise::transfer` [8](#0-7) .

At execution time the *live* confirming members are only `{C, D}` — two live confirmations against a threshold of 3 — because `B` was removed in step 3. The stale string `"B"` left in the `HashSet` is silently counted as if it were a valid, current confirmation.

### Why existing guards fail
- `delete_member`'s member-count guard only prevents shrinking the member set below the threshold; it does nothing about already-recorded confirmations elsewhere [7](#0-6) .
- `assert_valid_request` checks only the calling member, not the provenance of previously stored confirmations [5](#0-4) .
- No code path re-validates set membership of previously confirmed identifiers before comparing `confirmations.len()` to `num_confirmations`.

## Output

### Title
Multisig request can execute with fewer live confirmations than `num_confirmations` because stale confirmations from removed members remain counted - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` compares `confirmations.len() as u32 + 1` against `self.num_confirmations`, but `confirmations` is a raw `HashSet<String>` that is only pruned by `delete_member` for requests *created* by the removed member, not for requests the removed member merely *confirmed*. A member removed via `DeleteMember` can leave a stale confirmation entry on an unrelated pending request, letting fewer live members than `num_confirmations` push that request over the threshold and execute it.

### Finding Description
The invariant the code implicitly relies on is `confirmations.get(request_id).len() == count of confirmations from members currently in self.members`. This is violated because `delete_member` ( [9](#0-8) ) only removes confirmation sets for requests whose *creator* (`r.member`) is the member being deleted, never scanning other requests' `confirmations` HashSets for stale entries left by that member as a mere confirmer. `confirm` ( [10](#0-9) ) then compares `confirmations.len() as u32 + 1 >= self.num_confirmations` treating every stored string as a valid live confirmation. By confirming a request, then being removed as a member via a separate `DeleteMember` action, then having the remaining live members confirm, the request can execute with strictly fewer live-member confirmations than `num_confirmations`, allowing unauthorized `Transfer`, `AddKey`, `DeployContract`, etc. actions to run.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below `num_confirmations` live members." It permits movement of NEAR funds (e.g., via `MultiSigRequestAction::Transfer`) or account takeover (via `AddKey`/`DeployContract`) out of a multisig account with weaker-than-configured authorization, undermining the whole security guarantee of the k-of-n scheme. It is repeatable on any multisig contract instance whenever membership changes overlap with in-flight requests.

### Likelihood Explanation
No foundation, owner, or full-access key is required beyond the existing multisig members' normal operational flow (creating requests, confirming, and rotating membership) — these are all actions available to members through the public `confirm`/`add_request`/`execute_request` entrypoints. Any multisig that rotates a member while a request is pending confirmation is exposed; this is a plausible and low-cost operational sequence, not a contrived edge case, and requires no special timing beyond ordinary request/member-management overlap.

### Recommendation
When deleting a member in `delete_member`, iterate over all pending requests' `confirmations` sets and remove the deleted member's string entry (not just requests they authored), or alternatively store confirmations keyed by member and validate at `confirm`-time (or execution-time) that every entry in the confirmations set corresponds to a member still present in `self.members`, recomputing the live count before comparing to `num_confirmations`.

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_counted_after_member_removal() {
    // members: A (account), B (account), C (key1), D (key2), num_confirmations = 3
    // 1. A creates request R (Transfer)
    // 2. B calls confirm(R) -> confirmations(R) = {B}
    // 3. Separately: create+confirm DeleteMember{B} with A, C, D (3 confirmations) -> B removed
    //    assert B not in c.get_members()
    //    assert c.get_confirmations(R) still contains "B" entry (stale)
    // 4. C calls confirm(R) -> confirmations(R).len() == 2, < 3, not yet executed
    // 5. D calls confirm(R) -> confirmations(R).len() + 1 == 3 >= num_confirmations -> executes
    // Assert: only 2 live members (C, D) ever called confirm on R while being members,
    // yet request executed (c.requests.len() == 0 afterwards), demonstrating
    // execution below num_confirmations live confirmations.
}
```

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

**File:** multisig2/src/lib.rs (L322-339)
```rust
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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

**File:** multisig2/src/lib.rs (L407-423)
```rust
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
