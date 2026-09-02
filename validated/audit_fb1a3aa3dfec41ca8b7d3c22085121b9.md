This confirms the vulnerability path. The `delete_member` function at multisig2/src/lib.rs:356-379 only purges confirmations for requests that the deleted member themselves *created* (`r.member == member`), but never scans other pending requests' `confirmations: LookupMap<RequestId, HashSet<String>>` to strip out that member's vote if they had confirmed someone else's request. The `confirm` function at lines 292-315 counts `confirmations.len()` against `self.num_confirmations` without ever re-validating that each string in the `HashSet<String>` still corresponds to a current member in `self.members`.

### Title
Stale confirmations from removed multisig members count toward execution threshold, allowing sub-quorum execution - (File: multisig2/src/lib.rs)

### Summary
`confirm()` executes a request once `confirmations.len() + 1 >= num_confirmations`, but the `confirmations` set is never re-validated against the live `members` set, and `delete_member()` only clears confirmations on requests *created by* the removed member, not confirmations *given by* that member on other requests.

### Finding Description
`confirm` (multisig2/src/lib.rs:294-315) trusts the size of the per-request `HashSet<String>` in `self.confirmations` as a proxy for "number of live members that approved this request." `delete_member` (multisig2/src/lib.rs:356-379) removes the departing member from `self.members` and cleans up only requests where `r.member == member` (i.e., requests *originated* by that member), via: [1](#0-0) 
It never iterates `self.confirmations` to strip that member's string from confirmation sets of requests they merely *confirmed* (but did not create). So a confirmation recorded by member A on request X (created by B) survives A's removal from the multisig.

The equality this breaks: `confirmations counted for request X` should equal `live members who confirmed request X`, but after a member is deleted this becomes `confirmations counted (includes ghost votes) > live members who confirmed`.

### Impact Explanation
This lets a request execute (funds transfer, `AddKey`, `DeployContract`, `AddMember`/`DeleteMember`, arbitrary `FunctionCall`) with fewer *live* member confirmations than `num_confirmations` requires — i.e., a multisig request executed below threshold, which is explicitly listed as Critical impact (funds moved without proper authorization).

### Likelihood Explanation
This requires only ordinary multisig member interaction (no privileged bypass, no redeploy): a member confirms a pending request, is later removed via a normal `DeleteMember` request, and a subsequent legitimate confirmation from a remaining member pushes the stale count over threshold. In active K-of-N multisigs where confirmations often outlive quick membership churn, this is readily reachable.

### Recommendation
In `confirm()`, filter `confirmations` to only members currently present in `self.members` before comparing against `num_confirmations` (or equivalently, when a member is deleted, scan all `self.confirmations` entries and remove the deleted member's key from every set, not just requests they originated).

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2`.
2. B calls `add_request` creating request X (e.g., `Transfer` to attacker-controlled account).
3. A calls `confirm(X)` → `confirmations = {A}` (1/2, not yet executed), per `confirm` logic at multisig2/src/lib.rs:294-315.
4. Members B and C submit and confirm a `DeleteMember { member: A }` request (2 confirmations from B, C — legitimate threshold met) → A is removed via `delete_member` (multisig2/src/lib.rs:356-379); because request X was created by B (not A), its confirmation set `{A}` is untouched.
5. C now calls `confirm(X)` → `confirmations.len() (=1, from A) + 1 (C) = 2 >= num_confirmations (2)` → request X executes, transferring funds, even though only C is a genuine current member who approved it — the threshold of 2 live confirmations was never actually met. [2](#0-1) [3](#0-2)

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
