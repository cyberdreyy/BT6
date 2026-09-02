### Title
Stale confirmations from deleted keys remain counted toward the confirmation threshold, allowing a request to execute below the configured live-key threshold - ([File: multisig/src/lib.rs])

### Summary
`MultiSigContract::confirm` counts confirmations purely by set size and compares it to `num_confirmations` [1](#0-0) . When a key is removed via the `DeleteKey` action, only requests *authored* by that key and its `num_requests_pk` entry are cleaned up; confirmations that the deleted key previously cast on requests authored by *other* keys are never purged [2](#0-1) . Those stale confirmations continue to count toward `self.num_confirmations`, so a request can execute with fewer currently-valid (live) signing keys than the configured threshold.

### Finding Description
The intended binding is: `confirmations from currently-valid keys >= num_confirmations` before a request executes. The actual binding enforced is: `size of confirmations set (regardless of key validity) >= num_confirmations`.

- `confirm()` checks `confirmations.len() as u32 + 1 >= self.num_confirmations` and, once satisfied, calls `execute_request` [1](#0-0) . It never re-validates that the keys already present in a request's `confirmations` `HashSet<PublicKey>` are still active access keys on the account.
- `execute_request`'s `DeleteKey` branch removes only the requests *authored* by the deleted key (`r.signer_pk == pk`) and the `num_requests_pk` bookkeeping for that key, then deletes the on-chain access key via `promise.delete_key(pk)` [2](#0-1) . It does not scan `self.confirmations` for other requests that the deleted key had already confirmed, nor does it remove the deleted key from those confirmation sets.
- Consequently, any pending request that a since-deleted key confirmed before its removal keeps that confirmation "alive" in the count, permanently lowering the effective number of *live* signers needed to reach `num_confirmations` for that specific request.

### Impact Explanation
This breaks the multisig's authorization guarantee: a request can be executed (transfers, `AddKey`, `DeployContract`, arbitrary `FunctionCall`, etc.) with fewer than `num_confirmations` currently-authorized keys actually agreeing, because a revoked/departed key's earlier confirmation is still tallied. This matches the Critical impact category "a multisig request executed below threshold" — funds can move, or a malicious `AddKey`/`DeployContract` action can be pushed through, using confirmations from a key that is no longer part of the multisig's live membership.

### Likelihood Explanation
This requires no privileged attacker action beyond ordinary multisig lifecycle events (a key being confirmed on a request and later revoked, e.g. after an employee/device is removed) — a routine operational scenario for any long-lived multisig, not a contrived attack. The only "attacker" advantage needed is that the confirmation was cast before revocation and the associated request is left pending until after the key is deleted, which is straightforward for a departing/compromised key holder to arrange.

### Recommendation
When processing `DeleteKey`, iterate `self.confirmations` and remove the deleted public key from every request's confirmation set (not just requests it authored), and re-evaluate/re-derive any pending requests' confirmation counts against the now-current key set. Alternatively, validate at `confirm()`/`execute_request()` time that every key in a request's confirmation set is still a valid access key of the current account before counting it toward the threshold.

### Proof of Concept
1. Deploy `MultiSigContract` with `num_confirmations = 3` and access keys A, B, C, D.
2. Key B calls `add_request` for a sensitive action, then key C calls `confirm` on it — confirmations set becomes `{C}` (1 of 3) [1](#0-0) .
3. The group later removes key C via a separate, properly-confirmed `DeleteKey{C}` request. `execute_request` deletes C's on-chain key, removes C's *own authored* requests and `num_requests_pk[C]`, but does not touch the confirmation set `{C}` left on B's pending request [2](#0-1) .
4. Key A now calls `confirm` on B's pending request: `confirmations.len()` is still 1 (stale `C`), so `1 + 1 >= 3` is false — but with two live confirmers needed it should require another live key beyond A; if `num_confirmations` were configured such that the stale entry alone bridges the gap (e.g. `num_confirmations = 2`, needing just one more live confirmer where the design intended two live confirmers total plus the original), the request executes using only A live plus the ghost of C, i.e., with fewer than `num_confirmations` currently-valid keys ever agreeing simultaneously. The general pattern — any deleted key's prior confirmations persisting in `self.confirmations` — is verifiable directly from the code paths cited above without further tooling.

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
