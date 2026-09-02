### Title
Multisig requests can execute below the required confirmation threshold because confirmations from deleted/revoked keys are not purged from unrelated requests - (File: `multisig/src/lib.rs`)

### Summary
The `DeleteKey` action in `MultiSigContract::execute_request` only removes *requests that were originated* by the deleted public key; it does not remove the *confirmations* that key had already cast on other, still-pending requests. As a result, `confirm()`'s threshold check `confirmations.len() as u32 + 1 >= self.num_confirmations` can be satisfied by counting a vote from a key that is no longer a valid member of the multisig, allowing a request (e.g. a `Transfer`) to execute with fewer live, authorized confirmations than `num_confirmations` requires.

### Finding Description
`confirm()` stores confirming public keys per request in `self.confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` and executes the request once the set size (plus the current confirmer) reaches `num_confirmations`: [1](#0-0) 

When a key is removed via the `DeleteKey` action, the cleanup logic only targets *requests whose `signer_pk` (the request's creator) equals the deleted key* — it purges those requests/confirmations and the `num_requests_pk` counter for that key, but it never scans `self.confirmations` for entries where the deleted key appears as a *confirmer* on some other, unrelated, still-open request: [2](#0-1) 

Consequently, a confirmation cast by key `K` on request `R1` (created by a different key) remains permanently recorded in `confirmations[R1]` even after `K` is deleted from the multisig by a separate `DeleteKey` request. `assert_valid_request` also performs no liveness check on the members recorded inside `confirmations`: [3](#0-2) 

This breaks the intended custody binding "confirmations counted == live members that confirmed." The threshold check treats a stale (revoked) confirmation identically to a live one, so a request can reach `num_confirmations` using fewer than `num_confirmations` currently-authorized keys.

### Impact Explanation
This matches the "Critical" impact category of a multisig request executed below threshold. A malicious or compromised key `K` can confirm a high-value `Transfer` request before being revoked (e.g., because the operator detects compromise and deletes it). Its confirmation is never invalidated, so once the remaining, smaller set of legitimate keys casts just enough additional confirmations to reach `num_confirmations` (which now effectively includes the stale vote), the transfer executes — funds move under authorization that no longer represents `num_confirmations` live signers. This directly threatens NEAR held by the multisig account.

### Likelihood Explanation
The precondition is realistic and does not require any privileged access beyond normal multisig operation: a key just needs to confirm a request and later be removed (a common operational action when rotating/revoking compromised or departing signers) while that request is still pending. No collusion beyond the already-authorized key set is required, and the flaw is triggered by ordinary contract usage (`add_request` → `confirm` → later `DeleteKey` execution → `confirm` again), not by any edge-case signature replay.

### Recommendation
When executing `DeleteKey`, iterate over all entries in `self.confirmations` (not just requests originated by the deleted key) and remove the deleted public key from every confirmation set. Alternatively, validate at `confirm()`/execution time that every public key counted in `confirmations[request_id]` still corresponds to a live access key/member before allowing the threshold check to pass.

### Proof of Concept
Given `num_confirmations = 2` and 3 valid keys `A`, `B`, `C` on the multisig:
1. Key `A` calls `add_request` creating `R1 = Transfer{receiver_id: attacker, amount: X}` (`confirmations[R1] = {}`).
2. Key `B` calls `confirm(R1)` → `confirmations[R1] = {B}` (1 < 2, not yet executed).
3. Separately, keys `A` and `C` create+confirm `R2 = DeleteKey{public_key: B}` (because `B` is believed compromised) → `R2` executes: `B`'s access key is deleted, `num_requests_pk[B]` cleared, and any requests *created* by `B` are purged — but `confirmations[R1]` still contains `B`, since `R1` was created by `A`, not `B` (see `execute_request`'s `DeleteKey` branch, `multisig/src/lib.rs` lines 198-216).
4. Key `C` calls `confirm(R1)` → `confirmations[R1].len() + 1 == 2 >= num_confirmations` → `execute_request(R1)` runs, transferring `X` to `attacker`, even though only one currently-live key (`C`) plus one revoked key (`B`) authorized it — the required 2 *live* confirmations were never actually obtained.

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
