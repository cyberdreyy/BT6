### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing requests to execute below the intended K-of-N approval count - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests and confirmation records that were *authored* by the member being removed. It does not scrub confirmations that removed member previously cast as a co-signer on requests created by other members. Those stale confirmations remain in the `confirmations` `HashSet<String>` and are still counted by `confirm()` when checking `confirmations.len() as u32 + 1 >= self.num_confirmations`. This breaks the intended equality `num_confirmations == number of currently-live members who approved`, letting a request execute with fewer live-member approvals than the configured threshold.

### Finding Description
The contract stores confirmations per request as a set of member identifiers (`HashSet<String>`), separate from the `requests` map: [1](#0-0) 

`confirm()` determines whether to execute a request purely by counting entries already present in that set plus the current caller, with no re-validation that the members recorded in the set are still active members of the multisig: [2](#0-1) 

When a member is removed via `DeleteMember`, `delete_member` only cleans up requests where that member is the *original requester* (`r.member == member`), i.e., requests stored in the `requests` map that the member itself created. It never inspects or removes that member's confirmation entries recorded on requests authored by *other* members: [3](#0-2) 

Because `MultiSigRequestWithSigner.member` tracks only who *created* the request, and `delete_member` filters requests using that field, any confirmation the removed member cast as a co-signer on someone else's still-pending request survives untouched in `self.confirmations`. `assert_valid_request` (called from `confirm`/`delete_request`) also never re-checks that historical confirmers are still current members - it only validates the current caller: [4](#0-3) 

As a result, the binding the contract is supposed to enforce - `num_confirmations` live, currently-authorized member approvals required before executing a request - can be violated: a stale confirmation from a member who has since been removed (e.g., because they were compromised, offboarded, or otherwise untrusted) is silently counted toward the threshold for any request they confirmed before removal.

### Impact Explanation
This directly matches the "multisig request executed below threshold" Critical impact class. A `Transfer`, `FunctionCall`, `AddKey`, `AddMember`, or `DeployContract` request can be executed having received confirmations from fewer than `num_confirmations` currently-authorized members, because a stale confirmation from a now-removed member still counts. This can result in unauthorized transfers of NEAR, unauthorized key/member additions, or arbitrary contract redeployment/function calls being pushed through with weaker-than-configured authorization guarantees - undermining the entire K-of-N security model that the multisig is meant to provide.

### Likelihood Explanation
The precondition is realistic and requires no special external access: any legitimate `DeleteMember` action (itself performed through the normal multisig flow, not requiring foundation/owner privilege) that removes a member who had earlier confirmed a still-pending, unrelated request from a different requester will leave that stale confirmation in place. In active multisigs where requests can remain pending (up to `active_requests_limit` per member, with no forced expiry other than `delete_request`'s 15-minute cooldown), it is plausible that member turnover (which is the very trigger for wanting to remove a member, e.g. compromise) coincides with in-flight requests, making exploitation straightforward for the remaining members (potentially in collusion, or simply as an overlooked authorization gap).

### Recommendation
When removing a member in `delete_member`, iterate over `self.confirmations` (not just `self.requests` filtered by request author) and strip the removed member's identifier from every request's confirmation set, not only from requests it authored. Alternatively, re-validate at `confirm()`-execution time that every identifier in the stored confirmations set still corresponds to a current member of `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. Member `B` creates request `R` (e.g. `Transfer`) via `add_request` - stored with `member: B` in `requests`, and an empty `confirmations` set for `R`'s id.
3. Member `A` confirms `R` via `confirm(R)` → `confirmations[R] = {A}` (1 < 3, not yet executed).
4. Separately, the multisig approves and executes a `DeleteMember { member: A }` request (a normal 3-of-4 governance action removing `A`, e.g. because `A`'s key was suspected compromised). `delete_member` (`multisig2/src/lib.rs:355-379`) only removes requests where `r.member == A` - since `R.member == B`, `R` and its confirmation set `{A}` are left untouched.
5. Member `C` confirms `R` via `confirm(R)` → `confirmations[R].len() == 1`, `+1 == 2 < 3`, not executed yet.
6. Member `D` confirms `R` via `confirm(R)` → `confirmations[R].len() == 2`, `+1 == 3 >= 3` → `execute_request` is invoked (`multisig2/src/lib.rs:304-309`).
7. `R` executes having received real approval from only `C` and `D` (2 live members), because `A`'s stale confirmation - recorded before `A` was removed - was counted toward the 3-of-4 threshold, effectively executing the request with 2-of-3-live-members approval instead of the required 3.

### Citations

**File:** multisig2/src/lib.rs (L126-132)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
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
