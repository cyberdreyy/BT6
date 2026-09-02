### Title
Stale confirmations from removed multisig members are still counted toward the K-of-N execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` in `multisig2/src/lib.rs` only removes requests that were *created by* the member being deleted, and only removes `num_requests_pk` bookkeeping for that member. It does not scrub that member's confirmation entries from the `confirmations` set of *other, still-pending* requests that the member had previously confirmed. `confirm()` later counts `confirmations.len()` unconditionally, without checking that every entry in the set still corresponds to a live member. As a result, a request can be executed with fewer live-member confirmations than `num_confirmations` requires, because a stale confirmation from a member who has since been removed is still counted toward the threshold.

### Finding Description
`confirm()` computes whether to execute a request purely from the size of the stored confirmation set: [1](#0-0) 

```rust
pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
    self.assert_valid_request(request_id);
    let member = self.current_member().unwrap_or_else(...);
    let mut confirmations = self.confirmations.get(&request_id).unwrap();
    assert(!confirmations.contains(&member.to_string()), ...);
    if confirmations.len() as u32 + 1 >= self.num_confirmations {
        let request = self.remove_request(request_id);
        self.execute_request(request)
    } else {
        confirmations.insert(member.to_string());
        self.confirmations.insert(&request_id, &confirmations);
        PromiseOrValue::Value(true)
    }
}
``` [1](#0-0) 

`delete_member` is the only place that cleans up state on member removal, and it only purges requests whose *creator* (`r.member`) equals the removed member — it does not iterate over `confirmations` sets of other requests to strip out entries belonging to the removed member: [2](#0-1) 

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
        self.confirmations.remove(&request_id);
        self.requests.remove(&request_id);
    }
    self.num_requests_pk.remove(&member.to_string());
    self.members.remove(&member);
    ...
}
``` [2](#0-1) 

The filter is on `r.member` (the account that *created* the request), not on entries inside the `confirmations` HashSet of every request. Any request created by a *different* member, but previously confirmed by the member now being deleted, keeps that stale confirmation forever. `get_confirmations` and `confirm` never cross-check the confirmation set against `self.members`: [3](#0-2) 

The equality this breaks is: `num_confirmations` (K) should equal the number of *live* members who authorized a request, i.e. `len({confirmer ∈ confirmations : confirmer ∈ live_members})`. The code instead enforces `len(confirmations) >= K` without intersecting with current membership, so a removed member's earlier vote still counts.

### Impact Explanation
This is a direct authorization-threshold bypass: an arbitrary request (e.g. `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, `DeployContract`) can be executed with fewer live-member confirmations than the configured K-of-N threshold requires. This matches the "Critical" impact category "a multisig request executed below threshold." Funds controlled by the multisig account can be transferred, or the multisig's authority set/contract code can be altered, without a valid majority of currently-trusted members ever approving it in its final composition.

### Likelihood Explanation
Likelihood is realistic in normal contract operation, not a contrived edge case:
- Membership turnover (adding/removing members) is an explicitly supported, documented feature (`AddMember` / `DeleteMember`).
- It is common for a member to confirm one or more pending, not-yet-fully-confirmed requests and later be removed from the multisig (e.g., a departing signer, a compromised key being rotated out) while other requests they had confirmed remain outstanding.
- No special/malicious privilege is needed to trigger the bug: any of the remaining legitimate members completing confirmation on an old request will inadvertently cause the request to execute below the intended live threshold, because the code silently retains the departed member's vote.

### Recommendation
When a member is deleted, iterate all pending requests (not only those whose `r.member == member`) and remove the deleted member's entry from each request's `confirmations` set (or re-validate against `self.members` at count time). Concretely: in `delete_member`, for every `(request_id, confirmations)` in `self.confirmations`, remove `member.to_string()` from the set if present, and persist the trimmed set. Additionally, `confirm()`/`assert_valid_request` should recompute the effective confirmation count as `confirmations.iter().filter(|m| self.members.contains(m)).count()` rather than trusting `confirmations.len()` directly, so that a live-membership check is enforced even if stale entries are missed elsewhere.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `D` calls `add_request` to create request `R` (e.g., `Transfer` of the account's funds) — `confirmations(R) = {}`.
3. `A` calls `confirm(R)` → `confirmations(R) = {A}` (1 < 3, not yet executed).
4. `B` calls `confirm(R)` → `confirmations(R) = {A, B}` (2 < 3, not yet executed).
5. Members submit and confirm a `DeleteMember { member: A }` request and it executes (`self.members.len() - 1 = 3 >= num_confirmations = 3`, so the guard passes); `A` is removed from `self.members`. Since `R` was created by `D` (not `A`), `delete_member`'s cleanup loop does not touch `R`, and `confirmations(R)` still contains `A`.
6. `C` (a live member who never confirmed `R` before) calls `confirm(R)`. `confirmations(R).len() == 2`, `+1 == 3 >= num_confirmations`, so `execute_request(R)` runs and the `Transfer` is executed.
7. Result: `R` executed with confirmations from `{A(stale, removed), B, C}` — only 2 live members (`B`, `C`) actually authorized it at execution time, one fewer than the required `K = 3`, violating the K-of-N guarantee.

Note: I was not able to run this PoC against the actual compiled contract (no execution environment available here); the trace is derived directly from the logic in `confirm`, `delete_member`, and `remove_request` shown above, and I could not find any test in the indexed portion of `multisig2/src/lib.rs`/`multisig2/tests/general.rs` that exercises "member confirms a request created by someone else, then is removed, then a third member finishes confirmation" — the existing tests (`add_key_delete_key_storage_cleared`, `test_multi_add_request_and_confirm`) only cover deletion of a member's *own* requests, not stale confirmations left on others' requests.

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

**File:** multisig2/src/lib.rs (L464-470)
```rust
    pub fn get_confirmations(&self, request_id: RequestId) -> Vec<String> {
        self.confirmations
            .get(&request_id)
            .unwrap_or_else(|| env::panic_str("No such request"))
            .into_iter()
            .collect()
    }
```
