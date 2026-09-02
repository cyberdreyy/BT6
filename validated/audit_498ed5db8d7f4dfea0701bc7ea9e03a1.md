### Title
Confirmations from revoked multisig keys remain counted toward the confirmation threshold - ([File: multisig/src/lib.rs])

### Summary
`MultiSigContract::confirm` counts confirmations stored in `self.confirmations` toward `num_confirmations` without verifying that every confirming public key is still an active access key on the account. When a key is removed via the `DeleteKey` action, `execute_request` only purges requests and confirmations that key *originated* (`r.signer_pk == pk`), not confirmations that key previously *cast* on requests created by other keys. Those stale confirmations keep counting toward the threshold for those other requests, letting a request execute with fewer *live* confirming keys than `num_confirmations` requires — the same "sum still includes invalid entries" root cause as the reported `_vote()` bug, applied to the multisig confirmation tally instead of vote weight.

### Finding Description
The confirmation counting logic lives in `confirm`: [1](#0-0) 
It increments/checks the size of `confirmations` (a `HashSet<PublicKey>`) without re-validating that each key in the set is still a currently authorized signer for the account.

The only place stale confirmations are ever purged is inside `execute_request`'s `DeleteKey` handling: [2](#0-1) 
This filters `self.requests` by `r.signer_pk == pk` — i.e., it only removes requests *added* by the deleted key, and only removes confirmations attached to those specific requests. It does **not** scan `self.confirmations` for entries where the deleted key appears as a *confirmer* of a request added by someone else.

Binding broken: `confirmations.len()` (as used in the `+1 >= num_confirmations` check) is supposed to equal the number of confirmations cast by *currently live* multisig keys. After a `DeleteKey` execution, this equality no longer holds for any pending request that the deleted key had previously confirmed but did not create — the stored count still includes a key that is no longer a valid signer.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold" from the rules. A pending `Transfer` or other sensitive request (e.g., `AddKey`, `FunctionCall`) can be pushed to execution using fewer genuinely live confirmations than `num_confirmations` mandates, because one of the counted confirmations belongs to a key that has since been revoked (e.g., because it was compromised or an employee left). This weakens the k-of-n security guarantee the multisig is supposed to provide and can let a minority of live keys authorize fund transfers or account changes.

### Likelihood Explanation
This requires no privileged access beyond being one of the existing multisig key holders (an "unprivileged" party relative to the full n-of-n set) — a scenario is: a key later revoked for being compromised/lost had already confirmed a still-pending request before removal, or an insider deliberately confirms several pending requests before knowingly having their key rotated out, leaving stale confirmations behind. Since requests can stay open indefinitely (only subject to `REQUEST_COOLDOWN` for deletion, not to expiry on key changes), a stale confirmation can sit in a request for an extended time before it is exploited once the key is removed.

### Recommendation
When executing `DeleteKey`, iterate over all entries in `self.confirmations` (not just requests originated by `pk`) and remove `pk` from every confirmation set it appears in. Alternatively, validate at `confirm()` time (or at execution) that every public key present in a request's confirmation set is still an active access key on the account before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(3)` with three active keys `K1`, `K2`, `K3`.
2. `K1` calls `add_request` to create `R1` (e.g., `Transfer` to an attacker-controlled account). `K3` calls `confirm(R1)` → `confirmations[R1] = {K3}`.
3. Separately, `K1` submits and confirms a `DeleteKey { public_key: K3 }` request (reaching threshold through normal means), which executes and removes `K3` from the account per [3](#0-2) . Because `R1` was added by `K1` (not `K3`), the filter `r.signer_pk == pk` does not match `R1`, so `confirmations[R1]` still contains `K3` after `K3`'s key is deleted on-chain.
4. `K1` now calls `confirm(R1)` → `confirmations.len() (1) + 1 >= 3` is false, so it stores instead: `confirmations[R1] = {K1, K3}`.
5. `K2` calls `confirm(R1)` → `confirmations.len() (2) + 1 >= 3` is true, and `execute_request` runs the `Transfer`, even though only `K1` and `K2` are still valid, live keys — one fewer live confirmation than the configured `num_confirmations = 3`.

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
