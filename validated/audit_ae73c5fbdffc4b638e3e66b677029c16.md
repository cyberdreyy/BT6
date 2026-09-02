### Title
Stale Confirmations From Deleted Keys/Members Allow Multisig Requests To Execute Below The Live Confirmation Threshold - (File: multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
The `DeleteKey` action in `multisig/src/lib.rs` and the `DeleteMember` action in `multisig2/src/lib.rs` only purge pending requests and confirmations that were *authored* by the removed key/member. They do not purge confirmations that the removed key/member cast as a *confirmer* on requests authored by someone else. A revoked/compromised signer's stale confirmation therefore continues to count toward `num_confirmations`, letting a request execute with fewer currently-authorized signers than the configured threshold.

### Finding Description
When a request is confirmed, `confirm()` simply inserts the signer's public key into a `HashSet<PublicKey>` stored in `self.confirmations` and compares its length to `self.num_confirmations`: [1](#0-0) 

When a key is removed via the `DeleteKey` action inside `execute_request`, cleanup is scoped only to requests whose **original author** (`signer_pk`) matches the deleted key: [2](#0-1) 

This means: if key `K` merely *confirmed* (but did not author) some other pending request `R`, and `K` is later deleted by a separate `DeleteKey` request, `R`'s confirmation set still contains `K`'s public key. `assert_valid_request` never re-validates that recorded confirmers are still active keys on the account: [3](#0-2) 

So the equality the contract is supposed to guarantee — `confirmations counted == currently authorized live signers who approved` — is broken: `confirmations counted` can include ghosts from keys that have since been deleted. The identical pattern exists in `multisig2/src/lib.rs`'s `delete_member`, which only removes requests where `r.member == member` (the deleted member authored it), leaving confirmations by that member on other requests intact: [4](#0-3) 

### Impact Explanation
This matches the Critical impact category "a multisig request executed below threshold." A pending request (e.g. a `Transfer` action moving NEAR out of the multisig account) can accumulate a confirmation from a signer who is subsequently revoked (because they were compromised, offboarded, or malicious), yet that stale confirmation still counts. The request can then be pushed to execution by fewer *currently live* signers than `num_confirmations` mandates, effectively lowering the real approval threshold and allowing funds to move without the intended level of live authorization.

### Likelihood Explanation
This requires: (1) a signer confirms a pending request without being its author, (2) that signer's key is subsequently deleted via a separate `DeleteKey`/`DeleteMember` request (a realistic and expected operational event — key rotation, offboarding, compromise response), and (3) the original request remains pending and is later pushed over threshold by remaining live signers. Key rotation/offboarding while requests are pending is a normal multisig lifecycle event, so the precondition is plausible, not exotic, and requires no bug in NEAR's native access-key enforcement — only ordinary contract usage.

### Recommendation
When executing `DeleteKey` (multisig) or `DeleteMember` (multisig2), scan `self.confirmations` for every pending request and remove the deleted key/member from each confirmation set (not just requests it authored), re-checking whether removal drops any request below the required confirmation count. Alternatively, validate on `confirm()`/execution that every recorded confirmer is still a currently valid key/member before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize a multisig with `num_confirmations = 2` and keys `A`, `B`, `C`.
2. `A` calls `add_request` to create request `R1` (e.g., `Transfer` of contract funds to `A`).
3. `B` calls `confirm(R1)` → `R1` now has 1 confirmation (from `B`), one short of the threshold.
4. Team discovers `B`'s key is compromised. `A` and `C` create and confirm a `DeleteKey { public_key: B }` request, which executes and removes `B` from `num_requests_pk`/access keys, but the code path in `execute_request`'s `DeleteKey` handling only removes requests where `signer_pk == B` — `R1` was authored by `A`, not `B`, so `R1` and its confirmation set (still containing `B`) are left untouched.
5. `C` (a legitimate remaining signer) calls `confirm(R1)`. `confirmations.len() + 1 = 2 >= num_confirmations (2)`, so `R1` executes — moving funds via a promise — even though only one currently valid signer (`C`) ever approved it live at the time of execution; `B`'s stale, revoked confirmation supplied the second vote. [1](#0-0) [2](#0-1)

### Citations

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

**File:** multisig/src/lib.rs (L292-310)
```rust
    // Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert_eq!(
            env::current_account_id(),
            env::predecessor_account_id(),
            "Predecessor account must much current account"
        );
        // request must exist
        assert!(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed"
        );
        // request must have
        assert!(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests"
        );
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
