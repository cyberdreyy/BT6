## Analysis

I traced the "concurrent admin" bug class from the report onto the analogous binding explicitly listed in scope: **confirmations counted versus live members** in the `multisig`/`multisig2` contracts.

### Finding

In `multisig2/src/lib.rs`, `delete_member()` only purges confirmations for requests **created by** the removed member: [1](#0-0) 

```
fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
    ...
    let request_ids: Vec<u32> = self
        .requests
        .iter()
        .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
        .collect();
    for request_id in request_ids {
        self.confirmations.remove(&request_id);
        self.requests.remove(&request_id);
    }
    ...
}
```

This only cleans up requests where `r.member == member` (i.e., the removed member was the *creator*). It does **not** scan the `confirmations` map for other requests (created by other members) where the removed member had already added a **confirmation**. Those stale confirmations remain in the `HashSet<String>` for those requests.

`confirm()` only validates that the *current caller* is a live member via `assert_valid_request` → `current_member()`, but it never re-validates the members already present in the stored `confirmations` set: [2](#0-1) 

The same gap exists in the original `multisig/src/lib.rs` (v1): the `DeleteKey` action only removes confirmations for requests where `r.signer_pk == pk`, leaving that key's confirmations on other pending requests intact: [3](#0-2) 

### Impact

`confirm()`'s threshold check is `confirmations.len() as u32 + 1 >= self.num_confirmations` [4](#0-3) . Because stale confirmations from members who have since been removed still count toward this length, a request can execute (`Transfer`, `AddKey`, `FunctionCall`, etc.) with **fewer live-member approvals than `num_confirmations`** — i.e. `confirmations_counted != live_members_who_approved`. This directly matches the Critical-impact criterion "a multisig request executed below threshold."

Concretely, in a 2-of-3 multisig with members A, B, C:
1. A creates request R (`Transfer` of funds) — 0 confirmations.
2. B confirms R — 1 confirmation (B, A's creation does not auto-confirm unless `add_request_and_confirm` used).
3. Governance detects B is compromised and removes B via a separate `DeleteMember{B}` request (requires legitimate quorum) — B's confirmation on R is **not** cleaned up because R was created by A, not B.
4. C confirms R — count becomes 2 ≥ `num_confirmations` (2), and R executes — even though only A (creator, still uncounted as confirmer) and C are actually live members who confirmed; the required 2-of-{A,C} threshold was never truly met, only 1 genuine live confirmation (C) plus one stale, revoked confirmation (B).

This is exactly the "one admin action surviving/front-running removal of another admin" class from the report, materialized as a concrete custody violation: the multisig can move funds/execute privileged actions with confirmations attributable to accounts that are no longer members.

### Recommendation
When removing a member (`DeleteMember`/`DeleteKey`), scan **all** pending requests' confirmation sets (not just those the member created) and strip the removed member's/key's confirmation from each. Alternatively, have `confirm()` re-validate every entry in the stored confirmation set against the current `members` set before counting toward the threshold.

Note: I was unable to fully trace whether `add_request_and_confirm` or any other path further widens this gap, and did not have access to run the test suite to empirically confirm state after `DeleteMember`; this is a static-code-level finding based on the logic shown above.

### Title
Removed multisig member's stale confirmation still counts toward execution threshold - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

### Summary
`delete_member` (v2) / `DeleteKey` action (v1) only remove confirmations for requests that the removed member/key *created*, not confirmations they placed on requests created by others. `confirm()` never revalidates stored confirmations against the current member list, so a stale confirmation from a since-removed member can combine with fewer live confirmations to reach `num_confirmations` and execute a request.

### Finding Description
See analysis above — `delete_member`/`DeleteKey` filters requests by `r.member == member` / `r.signer_pk == pk` only, and `confirm()`'s threshold check counts `HashSet` length without filtering by current membership [5](#0-4) [2](#0-1) [3](#0-2) .

### Impact Explanation
Critical — a multisig request (including `Transfer`, `AddKey`, `FunctionCall`) can execute below the true live-member threshold, letting funds move or privileged state change with less genuine authorization than configured.

### Likelihood Explanation
Requires a legitimate member-removal event (e.g., due to compromise) to occur while that member has an outstanding confirmation on a still-pending request created by someone else — a realistic sequence in active multisig usage, especially the exact scenario the removal is meant to prevent (removing a compromised signer whose prior confirmations should no longer count).

### Recommendation
On member/key removal, purge that member's confirmation entry from every pending request's confirmation set, not only requests they created; or revalidate confirmer identities against current membership inside `confirm()`'s counting logic.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2`.
2. `A.add_request(R)` → creates R, 0 confirmations.
3. `B.confirm(R)` → 1 confirmation (B).
4. Multisig quorum executes `DeleteMember{B}` (a separate, properly-confirmed request) to remove B.
5. `C.confirm(R)` → confirmations length becomes 2 ≥ `num_confirmations`, R executes via `execute_request`, even though B is no longer a member and the actual live approving members are only C (plus creator A, uncounted) — one short of the intended 2 live approvals.

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

**File:** multisig2/src/lib.rs (L341-379)
```rust
    /// Add member to the list. Adds access key if member is key based.
    fn add_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        self.members.insert(&member.clone().into());
        match member {
            MultisigMember::AccessKey { public_key } => promise.add_access_key(
                public_key.into(),
                DEFAULT_ALLOWANCE,
                env::current_account_id(),
                MULTISIG_METHOD_NAMES.to_string(),
            ),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }

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
