Confirmed: in both `multisig/src/lib.rs` and `multisig2/src/lib.rs`, removing/deleting a member/key only purges requests where that key/member is the *original requester* (`r.signer_pk == pk` / `r.member == member`), but never scans other outstanding requests' `confirmations` sets to strip that key's already-recorded confirmation. This is the exact "confirmations counted versus live members" custody-binding break called out in the rules.

### Title
Stale confirmations from removed multisig members/keys still count toward execution threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
When a multisig key (v1) or member (v2) is removed via `DeleteKey`/`DeleteMember`, the contract only deletes requests that the removed key *originated*; it never removes that key's already-recorded `confirm()` votes from other outstanding requests. As a result, a request can execute with fewer than `num_confirmations` *currently live* signers, because a confirmation from a since-revoked member is still counted toward the threshold.

### Finding Description
In `multisig/src/lib.rs`, `confirm()` checks the size of the `confirmations: HashSet<PublicKey>` for a request against `self.num_confirmations`: [1](#0-0) 

When a `DeleteKey` action executes, cleanup only targets requests where the deleted key is the *original signer* of the request (`r.signer_pk == pk`); it does not touch `self.confirmations` entries belonging to other requests that this key merely confirmed: [2](#0-1) 

The same pattern exists in `multisig2/src/lib.rs`'s `delete_member`, which filters outstanding requests by `r.member == member` (the request's original creator) rather than scanning `confirmations` sets: [3](#0-2) [4](#0-3) 

The invariant the contract is supposed to enforce is: `confirmations_from_currently_live_members(request) >= num_confirmations` before a request executes. Because stale confirmations are never purged from unrelated in-flight requests, the actual enforced condition is `confirmations_ever_recorded(request) >= num_confirmations`, which can include votes from keys/members no longer part of the multisig. This breaks the equality between the trust the multisig's threshold is supposed to represent and the trust actually present at execution time.

### Impact Explanation
This falls under the Critical impact category "a multisig request executed below threshold." A K-of-N multisig's core guarantee is that any state-changing action (including NEAR `Transfer`, `DeployContract`, `AddKey`/`AddMember`, `FunctionCall`) requires K *currently authorized* signers. With this bug, an action can execute with fewer than K live signers if one confirming member/key had confirmed a request and was subsequently removed before the request reached quorum — the removed party's vote still counts. This directly enables NEAR to be moved, keys/members to be added, or contracts to be upgraded with authorization below the configured threshold.

### Likelihood Explanation
This requires normal multisig operation flow: (1) a member confirms request R (but R doesn't yet reach quorum), (2) that member is later removed via a separate, unrelated `DeleteKey`/`DeleteMember` request (a legitimate, expected administrative action, e.g. offboarding a departing signer or key rotation after a suspected compromise), and (3) remaining members continue confirming R, reaching quorum using the stale vote. This is a realistic and likely-to-occur sequence in any long-lived multisig with membership turnover — exactly the scenario key rotation/removal is meant to protect against, and the contract's own `Gotchas` documentation shows the team was aware of related edge cases around key removal but did not account for this one.

### Recommendation
When removing a key (`DeleteKey`) or member (`DeleteMember`), iterate all outstanding `requests`/`confirmations` entries and remove the deleted key's/member's entry from every request's confirmation set (not just requests it originally created), re-checking whether affected requests would violate an invariant (or simply leaving them below quorum is fine, since they must be re-confirmed). Alternatively, validate at `confirm()`-time / at quorum-check time that every recorded confirmation still corresponds to a currently active member/key before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` with members `[A, B, C]` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm` for `Transfer{amount}` to attacker-controlled `receiver_id` → request `R` has 1 confirmation (`A`).
3. `B` calls `confirm(R)` → `R` now has 2 confirmations (`A`, `B`), still below threshold (3), so `R` remains pending. [4](#0-3) 
4. Separately, the group (using quorum among `A`,`B`,`C`) submits and confirms a `DeleteMember{member: B}` request because `B`'s device was compromised. This executes `delete_member`, which only purges requests *originated by* `B`, leaving `R`'s confirmation set (`{A, B}`) untouched: [5](#0-4) 
5. `C` (the only unaffected live member besides `A`) calls `confirm(R)`. `confirmations.len() + 1 == 3 >= num_confirmations`, so `R` executes the `Transfer` — even though `B`'s confirmation is stale and only 2 currently-live members (`A`, `C`) actually authorized it. [6](#0-5)

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

**File:** multisig/src/lib.rs (L248-266)
```rust
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
