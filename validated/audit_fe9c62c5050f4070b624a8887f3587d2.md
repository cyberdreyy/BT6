### Title
Stale confirmations from removed multisig members are still counted toward `num_confirmations`, allowing request execution below the live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` only purges pending *requests* that were originally created by the member being removed, but does not scrub that member's *confirmations* from other still-pending requests. A confirmation cast by a member before removal therefore keeps counting toward `self.num_confirmations` after that member is deleted, letting a request execute with fewer live-member approvals than the configured threshold.

### Finding Description
`confirm()` compares the size of the `confirmations: HashSet<String>` for a request against `self.num_confirmations` and executes the request once the count is reached, without re-validating that every entry in the set still corresponds to a current member: [1](#0-0) 

`delete_member` removes the deleted member from `self.members`, and cleans up only the requests that member itself *originated* (`r.member == member`), deleting those requests and their confirmation sets. It does **not** iterate over other pending requests to remove the entries that member may have added to their `confirmations` set as a *confirmer*: [2](#0-1) 

Because `confirmations` is a plain `HashSet<String>` of member identifiers (line 128) rather than a live re-derivation from `self.members`, any confirmation recorded by a member before their removal survives the removal and is counted in the very next `confirm()` call on that same request.

Binding broken: `confirmations from members ∈ current self.members == num_confirmations required to execute`. After a `DeleteMember` action executes, this becomes `confirmations from members ∈ current self.members < num_confirmations`, yet `execute_request` still fires because the raw `HashSet` length still meets `num_confirmations`.

### Impact Explanation
This is a "multisig request executed below threshold" scenario (Critical per the rubric). A K-of-N multisig's entire security model rests on requiring K *currently authorized* approvals. If stale approvals from removed members still count, an attacker who is (or colludes with) a subset of members can get a request pre-confirmed by soon-to-be-removed members, then remove those members via a separate `DeleteMember` request, and finally push the original request over the threshold with fewer live confirmations than `num_confirmations` mandates — moving funds, deploying code, or adding access keys with an effectively lower quorum than configured.

### Likelihood Explanation
This does not require any privileged deployment misconfiguration or foundation/owner action — it only requires normal operation of the multisig (a request pending confirmation while a `DeleteMember` request is separately approved), which is a foreseeable operational sequence for any long-lived multisig. The bug is triggered purely by legitimate contract calls (`add_request`, `confirm`, `DeleteMember`), each performed by unprivileged/authorized-at-the-time members, so it is reachable without any social engineering, redeploy, or victim key.

### Recommendation
When executing `DeleteMember`, iterate over all pending requests' `confirmations` sets (not just requests originated by that member) and remove the deleted member's entry from each, or alternatively re-validate at `confirm()` time that every stored confirmation identifier is still present in `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R1)` (e.g., a `Transfer`) → `R1` has 1 confirmation (`A`).
3. `B` calls `confirm(R1)` → `R1` now has 2 confirmations (`A`, `B`), below threshold of 3, request stays pending.
4. Separately, members approve a `DeleteMember { member: B }` request (3 confirmations from `A, C, D`) — this executes via `execute_request` → `delete_member`, which removes `B` from `self.members` but only deletes requests where `r.member == B` (i.e., requests `B` originated). `R1` was originated by `A`, so `R1` and its confirmation set (still containing `B`) are untouched: [3](#0-2) 
5. `C` calls `confirm(R1)` → `confirmations.len() + 1 == 3 >= num_confirmations (3)` → `execute_request(R1)` fires per [4](#0-3) , even though the live confirming members are only `A` and `C` — one fewer than the required 3-of-4 quorum, because `B`'s stale confirmation was still counted.

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
