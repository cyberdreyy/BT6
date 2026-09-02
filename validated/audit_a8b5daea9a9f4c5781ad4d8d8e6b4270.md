### Title
Multisig executes a request using stale confirmations from deleted keys, bypassing the K-of-N threshold - ([File: multisig/src/lib.rs])

### Summary
`confirm()` counts entries in the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` set for a pending request to decide whether `num_confirmations` has been reached. `DeleteKey` only purges requests that a removed key originally *authored* (`r.signer_pk == pk`); it never scrubs that key's *confirmations* on requests it did not author. A pending request can therefore reach the configured threshold while counting a signature from a key that has since been deleted, letting it execute with fewer live signers than `num_confirmations` requires.

### Finding Description
`confirm()` reaches execution once `confirmations.len() + 1 >= self.num_confirmations`, purely a count of `PublicKey`s stored in the set — it never re-validates that those keys are still live access keys on the account: [1](#0-0) 

The `DeleteKey` action, when executed, cleans up only the requests originated by the removed key (`r.signer_pk == pk`), and clears the per-key request counter — it does **not** iterate `self.confirmations` to remove the deleted key's `PublicKey` from confirmation sets of *other* pending requests that it merely confirmed (but did not author): [2](#0-1) 

This breaks the equality the K-of-N scheme is supposed to guarantee:
`|confirmations[request_id]| == number of currently-live keys that approved request_id`

After a `DeleteKey` action runs, the left side can still include a key that no longer exists on the account (right side excludes it), so the stored count can reach `num_confirmations` while the number of *live* approving keys is one (or more) less.

### Impact Explanation
This is a critical, custody-breaking bug: it allows a `MultiSigRequest` (e.g. `Transfer`, `FunctionCall`, `DeployContract`) to be executed with fewer live signatures than the configured `num_confirmations` threshold — "a multisig request executed below threshold," which is an explicitly Critical-severity impact for this scan. Funds or privileged operations gated behind a K-of-N scheme can be moved/executed by a coalition smaller than K.

### Likelihood Explanation
No foundation, victim key, or redeploy is required — every actor involved (the members who add/confirm requests, and the member who initiates the `DeleteKey` request) is an ordinary multisig keyholder acting within their normal privileges. The only requirement is ordinary operational sequencing (a pending request partially confirmed, followed by a legitimate key-removal request), which is a realistic operational pattern (e.g. offboarding a compromised or departing signer) rather than a contrived edge case.

### Recommendation
When executing `DeleteKey` (and the analogous `DeleteMember` in `multisig2/src/lib.rs`), iterate all pending requests' `confirmations` sets and remove the deleted key/member from every set, not just the requests that key originally authored. Alternatively, revalidate at `confirm()`-time / execution-time that every `PublicKey` in a request's confirmation set still exists as an active access key on the account before counting it toward the threshold.

### Proof of Concept
Setup: `MultiSigContract::new(3)` on account `alice`, with 4 function-call access keys: A, B, C, D.

1. Key A calls `add_request_and_confirm(Transfer{amount})` → request `R` created with `signer_pk = A`; `confirmations[R] = {A}` (1/3).
2. Key B calls `confirm(R)` → `confirmations[R] = {A, B}` (2/3, below threshold, not executed): [1](#0-0) 
3. Separately, keys A, C, D create and confirm a `DeleteKey { public_key: B }` self-request and reach 3/3 confirmations, so it executes. The `DeleteKey` branch only deletes requests where `r.signer_pk == B` (none, since B never authored a request) — `R`'s `confirmations[R] = {A, B}` is left untouched, and B's access key is removed from the account: [2](#0-1) 
4. Key C now calls `confirm(R)` → `confirmations[R] = {A, B, C}`, count = 3 ≥ `num_confirmations` (3), so `execute_request` fires the `Transfer` immediately, even though B's key no longer exists on the account. The transfer executes with only 2 currently-live signers (A, C) having actually authorized it, not 3.

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
