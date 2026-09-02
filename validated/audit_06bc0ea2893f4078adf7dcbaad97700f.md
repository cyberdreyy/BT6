### Title
Stale confirmations from a removed multisig key remain counted toward the confirmation threshold, allowing a request to execute with fewer live keys than `num_confirmations` - (File: multisig/src/lib.rs)

### Summary
`MultiSigRequestAction::DeleteKey` only cleans up requests that were *created* by the removed key; it never scans the `confirmations` map for entries where the removed key had already confirmed a *different* request (one created by another signer). After the key is deleted from the account, its stale confirmation is still present in `self.confirmations` and still counts toward `num_confirmations` in `confirm()`, letting the remaining live keys push that request to execution with effectively fewer live confirming keys than the configured threshold.

### Finding Description
`confirm()` counts confirmations purely from the `HashSet<PublicKey>` stored per request and compares its size to `self.num_confirmations`: [1](#0-0) 

The `DeleteKey` action is supposed to purge a removed key's influence from the contract. It removes the key from `num_requests_pk` and deletes any *requests that key itself created*, but it filters strictly by `r.signer_pk == pk` (the request's original proposer), not by membership in the `confirmations` sets of other, still-pending requests: [2](#0-1) 

So if key `K` confirms request `R` (created by a different key), and is later removed via `DeleteKey{ public_key: K }`, request `R`'s confirmation set still contains `K`. `K`'s vote is never scrubbed from `R`. `remove_request()` also only clears confirmations for the request being finalized/removed, not for other requests a departed key had previously confirmed: [3](#0-2) 

This breaks the intended equality `confirmations counted == live signer keys who approved`. After `K` is deleted, the true number of live keys that approve `R` is `|confirmations(R)| - 1`, but the contract still evaluates `|confirmations(R)| >= num_confirmations`.

### Impact Explanation
This is a Critical-class issue per the given rubric ("a multisig request executed below threshold"): a request can be executed even though the number of *live* multisig keys that actually approved it is one less than the configured `num_confirmations`. In a small-`n` multisig (e.g. `num_confirmations = 2` of 3 keys), removing one key who had already confirmed a pending `Transfer` request effectively drops the live-approval threshold to `num_confirmations - 1` for that request, letting a single remaining key push through a fund transfer or `AddKey`/`FunctionCall` action that should have required one more live approver.

### Likelihood Explanation
This requires only ordinary multisig operation, no privileged foundation/owner action outside the multisig's own key set: (1) a request is created and confirmed by a subset of keys but not enough to execute, (2) one of the confirming keys is later removed via a normal `DeleteKey` request (e.g., routine key rotation, revoking a compromised or departing signer), (3) the remaining/malicious key holder(s) simply confirm the still-pending request to reach `num_confirmations`. No race condition or unusual timing is needed — the stale confirmation persists indefinitely until the request is confirmed or deleted.

### Recommendation
When executing `DeleteKey`, iterate over `self.confirmations` (not just `self.requests` filtered by `signer_pk`) and remove `pk` from every request's confirmation set, e.g.:
```rust
MultiSigRequestAction::DeleteKey { public_key } => {
    self.assert_self_request(receiver_id.clone());
    let pk: PublicKey = public_key.into();
    // remove pk from confirmations on all outstanding requests, not just ones it created
    let request_ids: Vec<u32> = self.confirmations.keys().collect();
    for request_id in request_ids {
        if let Some(mut confs) = self.confirmations.get(&request_id) {
            if confs.remove(&pk) {
                self.confirmations.insert(&request_id, &confs);
            }
        }
    }
    // still remove requests originally created by pk, and its num_requests_pk entry
    ...
}
```
Alternatively, re-validate at confirm-time that every public key in the stored confirmation set is still a current access key on the account before counting it toward the threshold.

### Proof of Concept
1. Deploy `MultiSigContract::new(2)` with keys `K1`, `K2`, `K3`.
2. `K1` calls `add_request` to create request `R` (e.g. `Transfer`).
3. `K2` calls `confirm(R)` → confirmations(`R`) = `{K2}` (1 < 2, not yet executed).
4. A separate `DeleteKey{ public_key: K2 }` request is created and confirmed (e.g. by `K1` + `K3`) and executes, removing `K2`'s access key from the account.
   - In `execute_request`'s `DeleteKey` branch, since `R`'s `signer_pk == K1` (not `K2`), `R` is *not* touched; confirmations(`R`) still `= {K2}`.
5. `K3` (or any remaining live key) calls `confirm(R)`. `confirmations(R).len() + 1 == 2 >= num_confirmations`, so `R` executes — even though only one currently-live key (`K3`) ever approved it, not the required two.

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
