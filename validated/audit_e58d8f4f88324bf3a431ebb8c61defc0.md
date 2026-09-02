### Title
Removed multisig key's stale confirmation still counts toward `num_confirmations`, allowing a request to execute below the effective live-signer threshold — (File: `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig/src/lib.rs` counts entries in the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` map to decide whether a pending request has reached `num_confirmations`. When a key is revoked via `MultiSigRequestAction::DeleteKey`, the cleanup logic only purges requests that were *added* by that key — it never scans other pending requests' `confirmations` sets to remove that key's prior votes. A stale confirmation from a since-removed key therefore remains valid and can be combined with confirmations from currently-live keys to push a request over threshold, letting it execute with fewer genuinely authorized (live) signers than `num_confirmations` requires.

### Finding Description
The binding that must hold is: `confirmations counted == confirmations from currently live keys`. The contract breaks this equality.

- `confirm()` reads the confirmation set for a request and executes it once `confirmations.len() + 1 >= self.num_confirmations`: [1](#0-0) 

- The only place that removes a public key's footprint from state is the `DeleteKey` action handler, which filters `self.requests` for entries where `r.signer_pk == pk` (i.e., requests *originated* by the deleted key), removes those requests plus their confirmation sets, and clears `num_requests_pk` for that key. It does **not** iterate `self.confirmations` to strip the deleted key from confirmation sets of *other*, still-pending requests that this key had previously confirmed: [2](#0-1) 

- `remove_request` (called from both `confirm` on success and `delete_request`) also has no logic to validate that the public keys inside a request's confirmation set are still active access keys of the account: [3](#0-2) 

Concretely, with `num_confirmations = 3` and members A, B, C:
1. A calls `add_request` for a pending request `R` (e.g., a `Transfer`) — `confirmations[R] = {}`.
2. B calls `confirm(R)` → `0+1 >= 3` false → `confirmations[R] = {B}`.
3. C calls `confirm(R)` → `1+1 >= 3` false → `confirmations[R] = {B, C}`.
4. Through normal multisig governance, C's key is revoked (`DeleteKey`) and a new key D is added (`AddKey`), both executed via separate fully-confirmed multisig requests. `confirmations[R]` is untouched by the `DeleteKey` cleanup because `R` was not *added* by C, only *confirmed* by C.
5. D calls `confirm(R)` → `confirmations[R].len() = 2` (`{B, C}`) `+1 = 3 >= 3` → `execute_request` runs.

The request executes using confirmations `{B, C, D}` even though C is no longer a valid key holder — only two currently-live members (B and D) actually authorized it, one short of the configured 3-of-n threshold.

### Impact Explanation
This is a threshold-bypass: a multisig request (including `Transfer`, `AddKey`/`DeleteKey`, `FunctionCall`, or `DeployContract`) can be executed with fewer live-key confirmations than `num_confirmations` mandates, because a revoked key's stale vote is still tallied. This maps directly to the Critical impact category "a multisig request executed below threshold," since NEAR (or contract state) can be moved or contract control changed by a party set that no longer meets the configured authorization requirement.

### Likelihood Explanation
The precondition — a pending request outliving a key rotation — is a routine operational sequence (key rotation/offboarding while requests are in flight), not a privileged bypass of the multisig itself. Any of the remaining live keyholders, acting entirely within their normal authority to `confirm`, can trigger execution once the stale vote plus new live votes reach the threshold; no compromise of a live key or special permission beyond ordinary usage is needed. The `REQUEST_COOLDOWN` (900s) does not prevent this, since `delete_request` requires an explicit call — a request left unconfirmed/unconfirmed-to-completion can simply sit pending across a key-rotation event.

### Recommendation
When executing `DeleteKey`, iterate over all entries in `self.confirmations` (not just requests added by the deleted key) and remove the deleted public key from every confirmation set. Alternatively, validate at `confirm()`/`execute_request()` time that every public key present in a request's confirmation set still corresponds to a live access key on the account before counting it toward `num_confirmations`.

### Proof of Concept
Extend the existing test harness in `multisig/src/lib.rs` (`mod tests`) following the pattern of `add_key_delete_key_storage_cleared` and `test_multi_3_of_n`:
1. Initialize `MultiSigContract::new(3)`.
2. As key A, `add_request` a `Transfer` request `R` to some receiver.
3. As key B, `confirm(R)` (confirmations = {B}).
4. As key C, `confirm(R)` (confirmations = {B, C}); note `assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 2)` and request `R` is still pending.
5. As key C (self-request to `alice()`), `add_request_and_confirm` a `DeleteKey{public_key: C}` request (with enough confirmations from A/B/C to pass in this simplified single-key test harness, or simulate via direct state manipulation for a 1-of-1 setup) to remove C's key.
6. Assert that `c.confirmations.get(&request_id_of_R)` still contains C's public key after the `DeleteKey` executes — this demonstrates the missing cleanup at [2](#0-1) .
7. Add a new key D, then call `confirm(R)` as D and observe `execute_request` fires (transfer promise created) even though only B and D are live confirming keys, one below `num_confirmations = 3`.

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
