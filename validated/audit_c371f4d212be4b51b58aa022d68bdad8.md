### Title
Confirmations from a revoked multisig key remain valid on other pending requests, allowing a request to execute below the intended live-signer threshold - (File: multisig/src/lib.rs)

### Summary
The `MultiSigContract`'s `DeleteKey` action only purges requests *originated* by the removed public key; it does not purge that key's existing *confirmations* recorded against other, still-pending requests. `confirm()` counts entries in the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` set without checking whether each recorded `PublicKey` is still an active access key on the account. This breaks the intended custody binding: "confirmations counted" should equal "confirmations from currently-live members."

### Finding Description
`execute_request`'s `DeleteKey` branch only cleans up requests where `r.signer_pk == pk` (requests the deleted key itself created): [1](#0-0) 

It never scans `self.confirmations` for entries containing the deleted `pk` that belong to *other* requests (i.e., requests created by a different key but confirmed by the key being deleted). Those confirmation sets are left untouched.

`confirm()` then determines whether to execute a request purely by the size of the stored confirmation set versus `num_confirmations`: [2](#0-1) 

There is no re-validation at confirm-time (or at delete-time) that every public key inside `confirmations.get(&request_id)` still corresponds to a live access key on the contract account. `assert_valid_request` only checks that the request and confirmation-set records exist, not that recorded signer keys are still authorized: [3](#0-2) 

### Impact Explanation
If a k-of-n multisig removes one of its n key-holders (e.g., an employee leaving, or a compromised key being revoked) via a `DeleteKey` request, any confirmation that revoked key had already placed on a *different*, still-open request remains counted toward that request's threshold. A remaining signer can then confirm that stale request and have it execute with the deleted key's confirmation still counted, effectively executing a multisig request with one fewer *live* confirmer than `num_confirmations` requires. This directly matches the Critical impact category "a multisig request executed below threshold," since the confirmation binding (confirmations counted == confirmations from live members) is broken.

### Likelihood Explanation
This requires only ordinary multisig operation, no attacker-controlled deployment or foundation privilege: any legitimate key rotation/removal event (a routine security practice) combined with a pre-existing open request leaves the contract in this state. No malicious validator, redeploy, or social engineering is needed — it is triggered by the normal `DeleteKey` action flow that the multisig itself supports.

### Recommendation
When executing `DeleteKey`, also iterate `self.confirmations` and remove the deleted `pk` from every confirmation set it appears in (not just requests it authored), or alternatively re-validate that each `PublicKey` in a confirmation set is still a valid key on the account before counting it in `confirm()`.

### Proof of Concept
1. Deploy a 3-of-3 (or any k-of-n, n>k) `MultiSigContract`.
2. Key A creates Request 1 (e.g., a `Transfer`). Key B confirms it (1/3 confirmations, not yet enough to execute).
3. Separately, the group creates and confirms a `DeleteKey` request removing Key B (e.g., because B's device was lost). This executes: `execute_request`'s `DeleteKey` branch only removes requests *authored by B*, so Request 1 (authored by A) is untouched, and B's confirmation on Request 1 remains in `confirmations`. [1](#0-0) 
4. Key A calls `confirm(Request 1)`. `confirmations.len()` (still includes B) `+ 1 >= num_confirmations` (3) is satisfied with only 2 *live* keys (A confirming, plus stale B), so the request executes. [4](#0-3) 
5. Result: a 3-of-3 multisig transfer executed with only 2 currently-live signers, breaking the threshold guarantee.

### Citations

**File:** multisig/src/lib.rs (L198-215)
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
