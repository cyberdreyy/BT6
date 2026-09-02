### Title
Stale confirmations from removed multisig members are not purged from other pending requests, allowing execution below the confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`delete_member` in the member-based multisig only deletes pending requests that were *created* by the removed member; it does not scrub that member's confirmation entries from *other* pending requests they had confirmed. Because `confirm` simply counts entries in the `confirmations` `HashSet` against `num_confirmations` without checking that each entry still corresponds to a current member, a request can be executed using a mix of live and stale (removed) member confirmations, causing the multisig to execute a request with fewer independent live-member approvals than `num_confirmations` requires.

### Finding Description
The contract tracks per-request confirmations as a `HashSet<String>` of member identifiers, keyed by `request_id`: [1](#0-0) 

When a member is removed via `DeleteMember`, `delete_member` only removes requests where the removed member is the original requester (`r.member == member`); it never touches the `confirmations` map entries for requests created by *other* members that this member had previously confirmed: [2](#0-1) 

`confirm` counts set membership generically, comparing `confirmations.len()` against `self.num_confirmations`, with no re-validation that every confirming identifier is still `self.members.contains(...)`: [3](#0-2) 

This breaks the intended equality: `live_member_confirmations(request) == num_confirmations` is required for execution, but the actual check is `total_confirmations(request) >= num_confirmations`, where `total_confirmations` can include identifiers no longer in `self.members`. This is a direct instance of the report's bug class — a global "required" threshold (here `num_confirmations`, analogous to the rollup's `requiredStake`) is checked against per-object state (`confirmations`) that is not re-validated when the underlying party's eligibility changes (a member being removed, analogous to a staker becoming inactive).

### Impact Explanation
This crosses the authorization/threshold boundary explicitly called out as in-scope: "a multisig request executed below threshold" is listed as a Critical impact. A `Transfer`, `AddKey`, `DeployContract`, `AddMember`/`DeleteMember`, or `FunctionCall` request can be executed with fewer genuinely-authorized (live) confirmations than the configured `num_confirmations`, letting a coalition smaller than the quorum move funds or change control of the account.

### Likelihood Explanation
This requires no privileged access beyond being (at some point) a legitimate multisig member who confirms a request and is later removed by the remaining members through normal governance — no foundation, redeploy, or external compromise is needed. Any multisig account that ever exercises `DeleteMember` (a normal operational action, e.g., replacing a departing team member or rotating a compromised key) while other requests are pending confirmation is exposed. The remaining members are unaware that a "confirmation" already counted toward quorum belongs to someone who is no longer a member, so they may unknowingly authorize execution with insufficient live confirmations.

### Recommendation
When executing `DeleteMember`, iterate all pending requests' `confirmations` sets (not only requests created by the removed member) and remove the deleted member's entry from each. Alternatively (and more robustly), when counting confirmations in `confirm`, filter the stored confirmation set to only those identifiers still present in `self.members` before comparing against `self.num_confirmations`, so removed members can never contribute to quorum regardless of when cleanup runs.

### Proof of Concept
1. Multisig initialized with `members = {A, B, C, D}`, `num_confirmations = 3`.
2. Member `B` calls `add_request` to create request `R` (e.g., a `Transfer`). `confirmations[R] = {}`.
3. Member `A` calls `confirm(R)`. `confirmations[R] = {A}` (1 < 3, not executed). [4](#0-3) 
4. Through a separate, properly-confirmed `DeleteMember { member: A }` request, `A` is removed: `delete_member` only inspects/removes requests where `r.member == A` (i.e., requests *A* created) — `R` was created by `B`, so `confirmations[R]` still contains `A`. [5](#0-4) 
5. `members` is now `{B, C, D}`, `num_confirmations` still `3`.
6. `C` calls `confirm(R)`: `confirmations[R] = {A, C}` (len 2, not yet executed).
7. `D` calls `confirm(R)`: `confirmations[R].len() + 1 = 3 >= num_confirmations(3)` → `execute_request(R)` runs.
8. Result: request `R` executes on only 2 live-member confirmations (`C`, `D`) plus a stale confirmation from the removed member `A`, even though 3 *current* members' approval was required — the multisig quorum was bypassed.

### Citations

**File:** multisig2/src/lib.rs (L126-128)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
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

**File:** multisig2/src/lib.rs (L355-374)
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
```
