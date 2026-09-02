### Title
Stale confirmations from deleted multisig members remain counted toward the confirmation threshold, allowing request execution below quorum - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts entries in the per-request `confirmations` `HashSet` toward `num_confirmations` without checking that every recorded confirmer is still a current member. `delete_member` only purges confirmations for requests that the *removed* member originally created (matched via `r.member == member`), not requests the removed member merely confirmed. As a result, a request can be executed once `confirmations.len() + 1 >= num_confirmations` even though one or more of the accounts in that set have since been removed from `self.members`, letting a request execute with fewer live-member approvals than the configured threshold.

### Finding Description
`add_request` initializes an empty confirmation set per request: [1](#0-0) 

`confirm` adds the calling member's identity string to that set and executes the request once the set size (plus the current confirmer) reaches `num_confirmations`: [2](#0-1) 

`delete_member` is supposed to clean up state tied to a removed member, but it only removes confirmations for requests where the removed member was the **original requester** (`r.member == member`), not requests where the removed member had merely added a confirmation via `confirm`: [3](#0-2) 

There is no code path that scans `self.confirmations` for stale entries belonging to a just-removed member on any request they did not originate. `assert_valid_request` also does not re-validate the members recorded inside a request's confirmation set: [4](#0-3) 

This breaks the intended equality: `confirmations.len()` (as counted in `confirm`) should equal the number of **live** members who approved the request, but instead it can include members who were removed after confirming but before the request reached quorum and executed.

### Impact Explanation
This is a Critical-severity issue per the multisig threshold-integrity guarantee: a request whose executed action (transfer, add/delete key, function call, etc.) is authorized based on `num_confirmations` distinct member approvals can instead execute with strictly fewer live-member approvals, because a stale confirmation from a removed member still counts. This directly matches "a multisig request executed below threshold."

### Likelihood Explanation
No attacker-controlled exploit is required beyond the ordinary multisig workflow: a member confirms a request, is later removed as part of routine membership rotation (a normal, non-malicious multisig operation), and the pending request they confirmed is not the request that removed them. Any subsequent confirmation by a remaining live member will use the stale count and can push the request past the reduced effective quorum. This is a realistic operational sequence, not a contrived edge case, so likelihood is high once membership changes while any other request is pending.

### Recommendation
When deleting a member, iterate the full `self.confirmations` map (not just requests where `r.member == member`) and strip the deleted member's identity string from every request's confirmation set; alternatively, in `confirm`, recompute the count by filtering `confirmations` against `self.members.contains()` before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request(R)` — `confirmations[R] = {}`.
3. `B` calls `confirm(R)` — `1 < 3`, so `confirmations[R] = {B}`.
4. `C` calls `confirm(R)` — `2 < 3`, so `confirmations[R] = {B, C}`.
5. Via a separate, already-quorum-reached self-request, `C` is removed with `delete_member`. Since `R` was created by `A` (not `C`), the filter `r.member == member` in `delete_member` does not match `R`, so `confirmations[R]` remains `{B, C}` even though `C` is no longer in `self.members`.
6. `D` (a live member) calls `confirm(R)`. `current_member()` succeeds for `D`; `confirmations[R].len() (2) + 1 = 3 >= num_confirmations (3)`, so `execute_request(R)` runs.
7. `R` executes despite only `B` and `D` being live members who ever approved it — one fewer live approval than the configured `num_confirmations = 3` threshold, because `C`'s stale confirmation was still counted after `C` was removed.

### Citations

**File:** multisig2/src/lib.rs (L188-199)
```rust
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
