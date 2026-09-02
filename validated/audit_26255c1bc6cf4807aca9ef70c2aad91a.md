### Title
Multisig `confirm()` counts stale confirmations from removed members, allowing execution below the configured threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` decides whether a request has enough approvals purely by counting the size of the `confirmations` `HashSet` stored for that `request_id`, without re-validating that every account/key already present in that set is still a live member of the multisig. `delete_member` only purges confirmation sets for requests that were *created* by the removed member; it never scans and strips the removed member's entry out of confirmation sets belonging to requests created by *other* members. This lets a request accumulate one confirmation from a member who is later removed, plus one confirmation from a currently-live member, and still hit `num_confirmations`, executing (e.g. a `Transfer`) with fewer live approvals than the configured threshold.

### Finding Description
`confirm()` only checks set membership/size: [1](#0-0) 

`assert_valid_request` verifies the *current caller* is a member, but never re-verifies the identities already stored inside `self.confirmations.get(&request_id)`: [2](#0-1) 

`delete_member` removes a member and only cleans up requests where that member is the *original creator* (`r.member == member`); it does not touch confirmation sets of other outstanding requests that the removed member may have already confirmed: [3](#0-2) 

The equality this breaks is: `confirmations counted for a request == confirmations from members who are members of the multisig at execution time`. Once a confirming member is deleted via `DeleteMember` (itself a normal multisig-approved action), any *other* pending request that member had already confirmed keeps that stale confirmation in `self.confirmations`, so `confirmations.len()` overstates the number of *live* approvers. When a remaining live member later confirms, `confirmations.len() as u32 + 1 >= self.num_confirmations` can become true using only 1 truly live approval (plus the stale one), letting `execute_request` run - including `MultiSigRequestAction::Transfer` - with confirmations below the intended threshold of live members.

The same structural bug exists in the legacy `multisig/src/lib.rs`, where `DeleteKey` only purges requests whose `signer_pk` equals the deleted key, not confirmation sets of other requests that key had confirmed: [4](#0-3) [5](#0-4) 

### Impact Explanation
This directly matches the Critical bucket "a multisig request executed below threshold" — NEAR (or any action, including adding a full-access key or deploying new contract code) can be moved/executed by fewer live approvers than `num_confirmations` requires, because a stale confirmation from a since-removed member is silently counted as valid.

### Likelihood Explanation
Any unprivileged member of the multisig (not the foundation, not requiring a redeploy) can set this up as part of normal operation: create/confirm a pending request, wait for that confirming member to be removed through a routine `DeleteMember`/`DeleteKey` governance action (membership churn is expected over the life of a multisig), then get one more live confirmation to push the stale request over the threshold. No malicious validator, RPC interception, or victim key compromise is required — only normal member turnover combined with a pre-existing pending, partially-confirmed request.

### Recommendation
When executing a request in `confirm()`, filter `confirmations` to only those entries that are still present in `self.members` before comparing against `num_confirmations`, or proactively purge/re-validate all confirmation sets (not just those created by the removed member) whenever `DeleteMember`/`DeleteKey` runs.

### Proof of Concept
1. Multisig has members A, B, C with `num_confirmations = 2`.
2. Member A calls `add_request` for `Transfer{ amount: X }` to an attacker-controlled account, without confirming (confirmations = {}).
3. Member B calls `confirm(request_id)` → confirmations = {B}, below threshold, request stays pending.
4. Separately, members later approve `DeleteMember{ member: B }` (a legitimate 2-of-3 governance action against a different request created by A or C, so the filter `r.member == member` for B's own requests does not touch the pending Transfer request from step 2).
5. B is now removed from `self.members`, but the pending request's `confirmations` set still contains B.
6. Member C calls `confirm(request_id)` → `confirmations.len() (1, containing stale B) + 1 >= 2` is true, so `execute_request` runs the `Transfer`, moving funds with only one truly live confirming member (C) instead of the required two. [1](#0-0) [3](#0-2)

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

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```
