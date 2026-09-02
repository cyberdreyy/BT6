Both `multisig/src/lib.rs` and `multisig2/src/lib.rs` share the same accounting flaw. I have enough evidence to write the finding for `multisig2/src/lib.rs` (the actively maintained variant with account-based membership), which most closely matches the "confirmations counted versus live members" custody binding called out in the rules.

### Title
Stale confirmations from deleted multisig members are counted toward the confirmation threshold, allowing requests to execute below the required number of live confirmations - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` decides whether to execute a request purely by counting entries in `self.confirmations[request_id]` against `self.num_confirmations`, without verifying that every counted confirmer is still a current member of `self.members`. `delete_member` only purges requests and confirmations for requests *originated* by the removed member; it never scans other requests' confirmation sets for a stale entry belonging to the removed member. As a result, a request can reach the confirmation threshold and execute even though the number of *live* member confirmations is strictly less than `num_confirmations`.

### Finding Description
`confirm` counts confirmations without checking member liveness: [1](#0-0) 

`delete_member` only cleans up requests where the removed member was the *creator* (`r.member == member`), leaving that member's confirmations on requests created by *other* members completely untouched: [2](#0-1) 

`current_member`/`assert_valid_request` only gate who is allowed to call `add_request`/`confirm` at call time; they never re-validate the *existing* entries already stored in `self.confirmations`: [3](#0-2) 

This breaks the intended custody binding of the contract:
`live confirmations counted == self.num_confirmations` before a `Transfer`/`FunctionCall`/`AddKey` request executes.
After a member is deleted, this becomes `live confirmations + stale confirmations >= self.num_confirmations`, i.e. the contract can execute a request that was never actually approved by `num_confirmations` currently-authorized members.

### Impact Explanation
This directly matches the Critical impact "a multisig request executed below threshold" from the accepted impact list. A `Transfer`, `AddKey`, `FunctionCall`, or `DeployContract` request can be pushed through by fewer live members than the multisig's own configured `num_confirmations`, undermining the entire K-of-N custody guarantee the contract is supposed to provide over the account's NEAR balance and access keys.

### Likelihood Explanation
This requires only ordinary, permitted operations already exposed by the contract: create a request, get it partially confirmed, then legitimately remove one of the confirming members via `DeleteMember` (itself a normal multisig-approved action), and finally have any remaining member call `confirm` again on the original, never-deleted request. No malicious/privileged bypass, no external tooling, no key theft is required — it is a pure state-accounting bug reachable through the contract's public `add_request`/`confirm`/`execute_request` flow.

### Recommendation
When executing `DeleteMember`, iterate all outstanding `self.confirmations` entries (not just requests created by the removed member) and strip the removed member's identifier from every confirmation set. Alternatively, when counting confirmations in `confirm`, filter `confirmations` to only those entries still present in `self.members` before comparing against `self.num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C], num_confirmations = 2)`.
2. `A` calls `add_request(request_X)` (request stored with `member: A`, empty confirmations).
3. `B` calls `confirm(request_X)` → `confirmations[request_X] = {B}` (len 1 < 2, not yet executed). [1](#0-0) 
4. `A` and `C` submit and confirm a `DeleteMember { member: B }` request (2-of-3, a legitimate governance action) → `B` is removed from `self.members`; `delete_member` does not touch `confirmations[request_X]` because `request_X.member == A`, not `B`. [2](#0-1) 
5. `A` calls `confirm(request_X)`. `confirmations[request_X].len() == 1` (`B`, stale) `+ 1 == 2 >= num_confirmations`, so `execute_request` runs `request_X` immediately — even though only `A`'s confirmation is from a currently live member. [4](#0-3) 

The request executes with only 1 live confirmation against a configured 2-of-3 threshold, violating the multisig's custody guarantee.

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

**File:** multisig2/src/lib.rs (L321-423)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
        }
    }

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

    /// Removes request, removes confirmations and reduces num_requests_pk - used in delete, delete_key, and confirm
    fn remove_request(&mut self, request_id: RequestId) -> MultiSigRequest {
        // remove confirmations for this request
        self.confirmations.remove(&request_id);
        // remove the original request
        let request_with_signer = self
            .requests
            .remove(&request_id)
            .unwrap_or_else(|| env::panic_str("Failed to remove existing element"));
        // decrement num_requests for original request signer
        let original_member = request_with_signer.member;
        let mut num_requests = self
            .num_requests_pk
            .get(&original_member.to_string())
            .unwrap_or(0);
        // safety check for underrun (unlikely since original_signer_pk must have num_requests_pk > 0)
        if num_requests > 0 {
            num_requests = num_requests - 1;
        }
        self.num_requests_pk
            .insert(&original_member.to_string(), &num_requests);
        // return request
        request_with_signer.request
    }

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
