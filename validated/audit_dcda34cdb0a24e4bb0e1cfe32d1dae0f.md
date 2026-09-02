## Title
Multisig Executes Requests Using Confirmations From Already-Removed Members - (File: `multisig2/src/lib.rs`)

### Summary
The `multisig2` (and `multisig`) contracts implement a K-of-N confirmation scheme where a request executes once `confirmations.len() >= num_confirmations`. However, when a member is removed via `delete_member`, the contract only purges confirmations for requests that were *created* by the removed member — it never purges confirmations that the removed member *gave* to requests created by other members. This is structurally the same bug class as the reported penguin issue: a count used for a security threshold is taken from a stale/permanent data structure (`confirmations`) instead of being reconciled against the live source of truth (`members`).

### Finding Description
`confirm()` counts votes purely from the `confirmations: LookupMap<RequestId, HashSet<String>>` map: [1](#0-0) 

`delete_member()` is the only place that cleans up `confirmations`, and it does so exclusively for requests whose **author** (`r.member`) equals the removed member — it never scans other pending requests to strip out confirmations that the removed member had already cast on them: [2](#0-1) 

`current_member()` and `assert_valid_request()` are only invoked when a member is actively calling `add_request`/`confirm`; there is no check, at execution time, that every entry inside a request's `confirmations` set still corresponds to a current member of `self.members`: [3](#0-2) 

The binding that should hold is:
`executed_request.confirmations ⊆ live_members`, and `|executed_request.confirmations ∩ live_members| >= num_confirmations`.

Instead, the code only guarantees `|executed_request.confirmations| >= num_confirmations`, regardless of whether some of those confirmers have since been removed from `self.members`. This is the exact analog of the report's `playerOwnedPenguins` (stale, append-only) vs `alivePenguins` (live) mismatch — here `confirmations` (stale/append-only per request) is checked against `num_confirmations`, but never reconciled with `members` (the live set).

The same root cause exists in the legacy `multisig` contract: `DeleteKey` in `execute_request` removes only requests created by the deleted key, not confirmations that key gave to other pending requests: [4](#0-3) 

### Impact Explanation
This breaks the multisig's core custody guarantee — a K-of-N threshold on custody of NEAR funds and privileged actions (transfers, key/member management, function calls). A request (e.g., a `Transfer` action moving NEAR out of the multisig account) can be executed with fewer *live* approving members than the configured `num_confirmations`, because a stale confirmation from an already-removed member still counts toward the threshold. Per the rubric this is Critical impact: "a multisig request executed below threshold."

### Likelihood Explanation
No privileged role beyond normal multisig member interaction is required. Any sequence of legitimate operations — a member confirms a pending request, that same member is later removed as part of routine membership rotation (e.g., key rotation, offboarding, compromise response), and another member later supplies the final confirmation — triggers the bug. Membership changes over the life of a long-running multisig are a normal, expected occurrence, making this highly likely to occur without any attacker needing to force unusual conditions.

### Recommendation
When executing (`confirm`) a request, filter `confirmations` to intersect with the currently live `self.members` set (or live access keys, for `multisig`) before comparing against `num_confirmations`. Alternatively, when a member is deleted, iterate over **all** pending requests' confirmation sets (not just those authored by that member) and strip the removed member's entry from each.

### Proof of Concept
Assume `num_confirmations = 3`, members = `{A, B, C, D}`.
1. `A` calls `add_request_and_confirm` to create request `X` (e.g. `Transfer` to an attacker-controlled account). `confirmations[X] = {A}` (1/3).
2. `B` calls `confirm(X)`. `confirmations[X] = {A, B}` (2/3) — not yet executed.
3. Separately, the multisig legitimately removes `B` via a `DeleteMember` request `Y` (reached quorum through unrelated confirmers, e.g. `A`, `C`, `D`). `delete_member` runs:
   - `request_ids` filtered by `r.member == B` — since `X` was authored by `A`, it is *not* included.
   - `confirmations[X]` is left untouched, still containing `B`.
   - `self.members.remove(&B)` — `B` is no longer a member.
4. `C` (a legitimate, current member) calls `confirm(X)`. `confirmations[X] = {A, B, C}`, `len() == 3 >= num_confirmations`, so `execute_request` fires the `Transfer` — even though only `A` and `C` are current, live confirming members (2 live confirmations), one short of the configured `3`.

Reference code paths: [5](#0-4)  and [6](#0-5) .

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

**File:** multisig2/src/lib.rs (L406-420)
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
