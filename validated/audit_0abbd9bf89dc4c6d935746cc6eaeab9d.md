### Title
Confirmations from a revoked multisig key are still counted toward the confirmation threshold, allowing a request to execute with fewer live signers than `num_confirmations` - (File: `multisig/src/lib.rs`)

### Summary
The `DeleteKey` action in `execute_request` removes only the *requests* that were created by the deleted key, but never scrubs that key's public key out of the `confirmations` sets recorded against *other* still-open requests. `confirm()` then counts entries in that stale `confirmations` set without checking whether each public key is still a valid access key on the contract, so a confirmation cast by an already-removed key can still push a request over the `num_confirmations` threshold.

### Finding Description
`confirm()` looks up the `HashSet<PublicKey>` of confirmations recorded for a `request_id` and compares its size to `self.num_confirmations`: [1](#0-0) 

Confirmations are keyed only by `PublicKey`, with no cross-check against the set of keys currently attached to the account. When a `DeleteKey` request is executed, the cleanup logic only removes *requests that were originally added by* the deleted key, plus the `num_requests_pk` counter for that key — it does not touch the `confirmations` map for other requests where that key had already voted: [2](#0-1) 

So the intended binding is:
```
confirmations_counted(request) == confirmations_by_currently_live_keys(request)
```
but the actual state after a `DeleteKey` execution is:
```
confirmations_counted(request) ⊇ confirmations_by_currently_live_keys(request)
```
i.e. it can include public keys that are no longer valid access keys on the account.

### Impact Explanation
This breaks the multisig authorization guarantee: `execute_request` — which can transfer NEAR, add/delete keys, deploy contracts, or make arbitrary function calls (`MultiSigRequestAction::Transfer`, `AddKey`, `FunctionCall`, etc., all dispatched from `execute_request`) — is gated solely on `confirmations.len() + 1 >= num_confirmations`. If a stale confirmation from a revoked key is one of the counted votes, a request can execute with fewer *live* signer approvals than the configured threshold, i.e. "a multisig request executed below threshold" per the specified Critical impact category (a party not entitled to approve, because their key was already revoked, still effectively contributes a vote toward a fund-moving or key-management action).

### Likelihood Explanation
Requires an unprivileged sequence achievable purely with valid keyholder actions already granted at some point: (1) key A confirms request X (added by key B, X not yet executed because more confirmations are needed), (2) a separate `DeleteKey` request removing key A is confirmed and executed by other keyholders, (3) request X remains open with key A's confirmation still counted in its `confirmations` set, (4) enough additional live-key confirmations bring the (stale-inclusive) total to `num_confirmations`, causing X to execute even though the number of currently-live approving keys is one less than intended. No foundation, owner, or out-of-scope privileged action is required — only ordinary multisig key holders acting in the order the contract already permits, and no reliance on ignoring documented initialization.

### Recommendation
When executing a `DeleteKey` action (or any key removal), iterate `self.confirmations` and remove the deleted public key from every request's confirmation set, or alternatively re-validate at `confirm()`/execution time that every public key in a request's `confirmations` set is still present among the account's current access keys before counting it toward `num_confirmations`.

### Proof of Concept
1. Contract initialized with `num_confirmations = 2`, keys `{A, B, C}` attached.
2. Key `B` calls `add_request` to create request `X` (e.g., a `Transfer`).
3. Key `A` calls `confirm(X)` → `confirmations[X] = {A}` (1 < 2, not yet executed) — see `confirm` logic at [1](#0-0) .
4. Key `B` and `C` create and confirm a separate request `Y` containing `DeleteKey { public_key: A }`, reaching the 2-confirmation threshold and executing it — `A` is removed as an access key, but only requests originally *added* by `A` and its `num_requests_pk` entry are cleaned up, per [2](#0-1) ; `confirmations[X]` still contains `A`.
5. Key `C` calls `confirm(X)` → `confirmations[X].len() + 1 = 2 >= num_confirmations (2)`, so `X` executes — approved by only one truly live key (`C`) plus a stale vote from the removed key `A`, one fewer live approval than the configured threshold.

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
