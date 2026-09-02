### Title
Stale confirmations from a removed member persist and count toward `num_confirmations` - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only purges the requests that the removed member *created*, but never scrubs that member's entry out of the `confirmations` `HashSet` on requests it merely *confirmed*. `confirm()` never re-validates that previously-recorded confirmers are still current members before comparing `confirmations.len() + 1 >= self.num_confirmations`, so a stale confirmation from a member who is no longer (or was only briefly) part of the multisig still counts toward quorum, letting a request execute with fewer live-member confirmations than `num_confirmations`.

### Finding Description
The invariant under test is: `distinct_live_member_confirmations_for(request) == num_confirmations_required_before_execution`. Tracing the code shows this equality can be violated.

- `confirm()` at [1](#0-0)  only checks that the *caller* is a current member (`self.current_member()`), then compares the size of the stored `confirmations` set (which may contain stale entries) against `num_confirmations`.
- `delete_member()` at [2](#0-1)  deletes outstanding requests and their confirmations only for requests where `r.member == member`, i.e. requests the removed member *created*. It also removes the member's `num_requests_pk` entry. It does **not** scan other members' active requests to strip the removed member's vote out of `confirmations` for requests they merely confirmed.
- `remove_request()` / the confirmations `LookupMap` is keyed purely by `request_id -> HashSet<String>`; nothing links entries in that set back to current membership at read time.

Exploit flow (no re-add needed to prove the break, but re-add is an equally valid variant since it does not remove the stale entry either):
1. Members `{A,B,C,D}`, `num_confirmations = 3`. `A` creates request `R` (a `Transfer`).
2. `B` calls `confirm(R)` → `confirmations[R] = {B}`. `C` calls `confirm(R)` → `confirmations[R] = {B,C}` (2 of 3, not yet executed).
3. A separate request `DeleteMember{B}` is created and confirmed by `A, C, D` and executes, removing `B` from `members` and clearing `num_requests_pk[B]`. Because `R` was created by `A` (not `B`), `delete_member`'s cleanup loop never touches `R`, so `confirmations[R]` still equals `{B,C}`.
4. `D` (a genuine, current member) calls `confirm(R)`. `current_member()` returns `D` (valid), `confirmations.contains(D)` is false, and `confirmations.len() + 1 == 3 >= num_confirmations(3)` → `R` executes via `execute_request`, moving funds.

At execution time, the live members are `{A, C, D}`, yet the quorum was reached using `B`'s (a non-member's) stale vote plus `C` and `D` — only 2 of the 3 confirmations came from currently-authorized members. `assert_valid_request` [3](#0-2)  only checks the caller's current membership and that the request/confirmation map entries exist — it never re-validates the *existing* confirmer set. `current_member()` [4](#0-3)  is only used to validate the caller, not historical confirmers.

The "remove and re-add" variant described in the question is a special case of the same root cause: if `B` is later re-added, `B`'s stale confirmation is still sitting in `confirmations[R]` from before removal (re-adding doesn't clear or refresh it), and `num_requests_pk[B]` is reset to 0 by the `add_member`/`delete_member` pair — but this counter is unrelated to `confirmations` and its reset has no bearing on the confirmation-count check. The decisive bug is that `confirmations` entries are never invalidated when membership changes, regardless of whether the member is re-added or stays removed.

### Impact Explanation
A multisig request (e.g. a `Transfer` action moving NEAR out of the multisig account) can be executed with fewer distinct *live* member confirmations than `num_confirmations` mandates, because a departed member's stale vote is silently counted. This directly matches the listed Critical impact: "a multisig request executed below `num_confirmations` live members," and results in unauthorized movement of the multisig's funds. The blast radius is any multisig deployed from this contract that has ever removed a member who had confirmed (but not created) an active request — the ghost vote persists indefinitely until that specific request is executed or explicitly deleted.

### Likelihood Explanation
This does not require an "unprivileged attacker" acting alone against a victim they don't control, but it is a live risk for any multisig using this contract: it requires normal, expected multisig operations (a member confirms a request created by someone else, then that member is later removed via a legitimate `DeleteMember` request) — no malicious deployment parameters or foundation privileges are needed. Once membership changes as described, any remaining member (potentially a minority faction) can push through execution of an old, under-confirmed request. This is fully reproducible deterministically and repeatable for every request left in this state.

### Recommendation
When removing a member in `delete_member`, iterate all active requests' `confirmations` sets (not just requests the member created) and strip the removed member's identity from every entry; alternatively, revalidate at `confirm()` time (and before executing) that every confirming identity currently exists in `self.members`, discarding stale entries or excluding them from the quorum count.

### Proof of Concept
```rust
// cargo test, near-sdk testing_env!, in multisig2/src/lib.rs tests module
#[test]
fn test_stale_confirmation_counts_after_member_removed() {
    // members A, B, C, D; num_confirmations = 3
    // 1. A creates request R (Transfer to some receiver)
    // 2. switch context to B -> c.confirm(R)   => confirmations[R] = {B}
    // 3. switch context to C -> c.confirm(R)   => confirmations[R] = {B, C}, request NOT executed (2 < 3)
    // 4. A creates request R2 = DeleteMember{B}; A, C, D confirm R2 -> executes, members = {A, C, D}
    //    assert!(c.get_members() does not contain B)
    // 5. assert_eq!(c.get_confirmations(R).len(), 2); // still {B, C} despite B removed
    // 6. switch context to D -> c.confirm(R)
    //    -> confirmations.len() (2) + 1 >= num_confirmations (3) -> executes R
    // 7. assert_eq!(c.requests.len(), 0); // R executed using B's stale vote + C + D,
    //    i.e. only 2 of 3 confirmations (C, D) came from currently-live members.
}
```

### Citations

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
