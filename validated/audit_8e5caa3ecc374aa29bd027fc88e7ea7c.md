## Analysis Result [1](#0-0) 

### Title
Stale confirmations from removed multisig members still count toward the confirmation threshold, allowing requests to execute below the required number of live approvers - (File: multisig2/src/lib.rs, multisig/src/lib.rs)

### Summary
`delete_member` (in `multisig2`) and the analogous `DeleteKey` action (in the legacy `multisig`) only purge the *outstanding requests authored by* the removed member/key. They never scrub that member's *confirmations recorded on other pending requests*. Since `confirm()` counts `confirmations.len()` regardless of whether those confirming identities are still members, a request can later execute using one or more confirmations from an account that is no longer part of the multisig, letting `K-1` (or fewer) *live* members push a request past the `num_confirmations` threshold.

### Finding Description
The invariant the multisig is supposed to guarantee is:
```
confirmations_that_count_toward_threshold == confirmations_from_current_live_members
```

In `multisig2/src/lib.rs`, `confirm()` simply compares the size of the stored confirmation set to `num_confirmations`: [2](#0-1) 

`delete_member()` removes the departing member from `self.members` and deletes only the requests **authored** by that member — it does not walk `self.confirmations` to strip that member's signature off requests authored by *other* members: [3](#0-2) 

Scenario:
1. Multisig has members `{A, B, C}`, `num_confirmations = 2`.
2. `A` calls `add_request` to create `R1` (e.g. `Transfer`). `B` calls `confirm(R1)` → confirmations = `{B}` (1 of 2, pending).
3. Separately, `A` and `C` confirm a `DeleteMember { member: B }` request, which executes (`2 >= 2`), removing `B` from `self.members`. `R1`'s confirmation set still contains `B`.
4. `A` now calls `confirm(R1)`. `confirmations.len() + 1 == 2 >= num_confirmations`, so `R1` executes — even though `B` is no longer a member. The transfer was authorized by only one live member (`A`) plus a stale confirmation from a removed account, not by two live members.

The exact same class of bug exists in the legacy `multisig/src/lib.rs`, where `DeleteKey` only removes requests signed by that key, not that key's confirmations on other people's requests: [4](#0-3) [5](#0-4) 

This is a direct analog of the external report's root cause: a value used to satisfy a validity/threshold check (`DISPUTED_L2_BLOCK_NUMBER`/confirmation count) is not re-validated against the *current, authoritative context* (claimed block number cap / live member set) before being trusted, letting stale state satisfy a check it should no longer satisfy.

### Impact Explanation
This breaks the "K of N" custody guarantee that the multisig is supposed to enforce: a `Transfer`, `AddKey`, `FunctionCall`, or other privileged request can be executed with fewer live/authorized confirmations than `num_confirmations` requires, because a removed member's old confirmation is silently still counted. Since `Transfer` moves NEAR out of the multisig account, this is a critical impact — "a multisig request executed below threshold" as defined in the assessed impact list.

### Likelihood Explanation
This requires no privileged access beyond being an existing member who confirms a request before being removed (or being removed after confirming, which can happen through normal churn — e.g. a departing employee's key is deleted after they already confirmed a pending transfer). No malicious deployer, foundation account, or victim key theft is needed; it only depends on the ordinary member-removal workflow that the contract explicitly supports (`DeleteMember`/`DeleteKey`) combined with normal pending-request handling. This makes it a realistic operational scenario rather than a purely theoretical one.

### Recommendation
When removing a member (`delete_member` in `multisig2`, and the `DeleteKey` action in `multisig`), iterate over `self.confirmations` for all pending requests and remove the departing member's/key's confirmation entry from every request's confirmation set (not just from requests they authored). Alternatively, validate at `confirm()`-time / execution-time that every recorded confirming identity in the set is still a current member before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new([A, B, C], 2)`.
2. `A` (as predecessor/signer) calls `add_request({receiver_id: multisig, actions:[Transfer{amount}]})` → `request_id = R1`.
3. `B` calls `confirm(R1)` → confirmations for `R1` = `{B}` (returns `PromiseOrValue::Value(true)`, not yet executed).
4. `A` calls `add_request_and_confirm({receiver_id: multisig, actions:[DeleteMember{member: B}]})`, then `C` calls `confirm(...)` on that request → executes, `B` is removed from `self.members`; `R1` is untouched because it was authored by `A`, not `B`.
5. `A` calls `confirm(R1)`. `self.confirmations.get(&R1)` still contains `{B}`, so `confirmations.len() + 1 == 2 >= num_confirmations (2)` → `execute_request` runs the `Transfer`, even though only `A` is currently a live member confirming it. [6](#0-5)

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
