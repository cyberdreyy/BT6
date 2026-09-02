### Title
Multisig executes requests using stale confirmations from removed keys, allowing execution below the live-key threshold - (File: `multisig/src/lib.rs`)

### Summary
The `MultiSigContract::confirm` function counts confirmations already stored in `self.confirmations` toward the `num_confirmations` threshold without verifying that every public key in that confirmation set still holds an active access key on the account. When a key is removed via a `DeleteKey` request, the contract only purges *requests originated by* that key — it never purges *confirmations that key previously cast on other requests*. As a result, a request that has already received one or more confirmations from a key that is later deleted keeps counting those stale confirmations, letting the request execute (e.g. transfer funds, delete/add keys, call arbitrary functions) with fewer currently-valid keyholders approving than `num_confirmations` requires.

### Finding Description
`confirm()` reads the confirmation set for a request and checks only the *size* of the set against the threshold, plus that the calling key hasn't already confirmed: [1](#0-0) 

`execute_request` handles `MultiSigRequestAction::DeleteKey` by removing outstanding *requests* whose `signer_pk` equals the deleted key, and by removing the `num_requests_pk` counter entry for that key — but it never scans `self.confirmations` to strip that key out of confirmation sets on other, still-pending requests: [2](#0-1) 

`remove_request`, used by `confirm`/`delete_request`/`DeleteKey`, likewise only clears the confirmation set for the request being removed, not confirmations left by a deleted key on *other* requests: [3](#0-2) 

The custody/authorization binding this contract is supposed to enforce is: `count(confirmations from keys still on account) >= num_confirmations` before a request executes. Because deleted keys' prior confirmations are never invalidated, the actual invariant enforced is `count(confirmations ever recorded, live or dead) >= num_confirmations`, which can diverge from the intended live-member threshold — exactly the "confirmations counted versus live members" custody binding called out in scope.

### Impact Explanation
This lets a request (Transfer, FunctionCall, AddKey, DeployContract, etc.) execute with fewer live/authorized signers than `num_confirmations` mandates, because a stale confirmation from an already-removed key still counts toward the threshold. This directly matches the Critical impact category "a multisig request executed below threshold," since NEAR/state changes controlled by the multisig (fund transfers, key management, contract calls) can be pushed through by a minority of currently valid keyholders.

### Likelihood Explanation
No special privilege beyond being one of the multisig's own keyholders is required — the ordering (confirm with a key, later have that key removed via an unrelated `DeleteKey` request, then have the remaining minority confirm) is a normal-looking sequence of legitimate multisig operations (key rotation is an expected, documented workflow for this contract) with no exotic preconditions, malicious node, or foundation involvement. It only requires that a request receive at least one confirmation before the confirming key is later deleted, which is plausible in ordinary key-rotation/offboarding flows.

### Recommendation
When executing `DeleteKey`, iterate `self.confirmations` and remove the deleted `public_key` from every stored confirmation `HashSet`, not just from requests whose `signer_pk` matches. Alternatively, at `confirm()` time, re-validate that all public keys in a request's confirmation set are still present among the account's current access keys before counting them toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract` with `num_confirmations = 3` and access keys `K1, K2, K3, K4`.
2. `K1` calls `add_request` to create request `A` (e.g., `Transfer`).
3. `K2` calls `confirm(A)` → `confirmations[A] = {K2}` (size 1, below threshold, per [1](#0-0) ).
4. `K1` creates and (via `K3`, `K4`) confirms a separate request `B = DeleteKey{ K2 }`, which reaches threshold and executes, deleting `K2`'s access key. Per [2](#0-1) , only requests signed by `K2` are purged; `confirmations[A] = {K2}` is untouched.
5. `K3` calls `confirm(A)` → `confirmations[A] = {K2, K3}` (size 2, still below 3).
6. `K4` calls `confirm(A)` → size check becomes `2 + 1 = 3 >= 3` → `execute_request(A)` runs, even though only `K3` and `K4` (2 of the remaining 3 live keys `K1, K3, K4`) ever actually approved `A` while it was live; `K2`'s confirmation is a stale artifact from a deleted key.

This demonstrates request `A` executing below the intended live-keyholder threshold.

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

**File:** multisig/src/lib.rs (L272-291)
```rust
    fn remove_request(&mut self, request_id: RequestId) -> MultiSigRequest {
        // remove confirmations for this request
        self.confirmations.remove(&request_id);
        // remove the original request
        let request_with_signer = self
            .requests
            .remove(&request_id)
            .expect("Failed to remove existing element");
        // decrement num_requests for original request signer
        let original_signer_pk = request_with_signer.signer_pk;
        let mut num_requests = self.num_requests_pk.get(&original_signer_pk).unwrap_or(0);
        // safety check for underrun (unlikely since original_signer_pk must have num_requests_pk > 0)
        if num_requests > 0 {
            num_requests = num_requests - 1;
        }
        self.num_requests_pk
            .insert(&original_signer_pk, &num_requests);
        // return request
        request_with_signer.request
    }
```
