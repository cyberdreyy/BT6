### Title
Stale confirmations from deleted multisig keys allow request execution below the true live-key threshold - (File: multisig/src/lib.rs)

### Summary
The `multisig` contract's `DeleteKey` request action removes a public key's outstanding *self-initiated* requests and its `num_requests_pk` counter, but never purges that key's existing confirmations recorded on other, still-pending requests. Because `confirm()` counts confirmations purely by set membership/size without re-validating that each confirming key is still live, a request can reach `num_confirmations` and execute even though one or more of the "confirming" keys were already deleted from the account.

### Finding Description
`execute_request` handles `MultiSigRequestAction::DeleteKey` as follows: [1](#0-0) 

It only removes requests where `r.signer_pk == pk` (i.e., requests *created* by that key) and clears `num_requests_pk` for that key. It does **not** scan `self.confirmations` to strip `pk` from confirmation sets of *other* pending requests that this key had already confirmed.

`confirm()` then trusts the stored confirmation set size against the live `num_confirmations` threshold: [2](#0-1) 

The binding this is supposed to maintain is:
```
confirmations.len() (for a request) == number of currently-authorized keys that approved it
```
After a `DeleteKey` execution, this equality breaks for any request that the deleted key had confirmed before removal — the stale entry remains in the `HashSet<PublicKey>` for that request's confirmations, inflating the count relative to the actual number of live, authorized keys.

### Impact Explanation
This falls under the Critical impact category: "a multisig request executed below threshold." Concretely, with `num_confirmations = K` and `N` total keys:
1. Key A confirms Request X (added to confirmations, X now has 1/K).
2. Independently, the account rotates/removes key A via a separate `DeleteKey` request (a normal, legitimate multisig operation, e.g., key rotation or compromise response) — this executes and removes A's *own* requests/counter but leaves A's confirmation on X intact.
3. Now only `N-1` live keys exist, but Request X still shows A's confirmation counted toward K.
4. The remaining live members confirm X until `confirmations.len()+1 >= K` is satisfied — X executes with fewer live-key approvals than `K`, i.e., below the actual quorum the account is supposed to require after the rotation.

This directly breaks the multisig's core custody guarantee: an arbitrary `Transfer`, `AddKey`, `DeployContract`, etc. can be executed while only `K-1` (or fewer) currently-authorized keys actually approved it.

### Likelihood Explanation
Key rotation/removal via `DeleteKey` is an expected, common multisig operation (e.g., replacing a lost/compromised device key). Any request left pending with a partial confirmation at the time a confirming key is deleted will silently retain that stale confirmation. No attacker needs elevated privilege beyond what a normal multisig member already has (submitting/confirming requests and requesting key rotation) — the flaw is in the contract's bookkeeping, not in violating access control.

### Recommendation
When executing `MultiSigRequestAction::DeleteKey`, iterate all entries in `self.confirmations` and remove `pk` from every confirmation set (not just delete the requests originated by `pk`). Alternatively, revalidate confirmation sets in `confirm()`/`assert_valid_request()` against the current active key set (e.g., by tracking active keys explicitly and filtering confirmations lazily) before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(3)` with keys `A, B, C`.
2. `A` calls `add_request_and_confirm(request_X)` → confirmations(X) = `{A}` (1/3).
3. Separately, `B` and `C` create+confirm a `DeleteKey{public_key: A}` request and execute it (2/3, self-request) — this removes A's requests created by A and `num_requests_pk[A]`, but confirmations(X) is untouched, still `{A}`.
4. `B` confirms X → confirmations(X) = `{A, B}` (2/3).
5. `C` confirms X → `len()+1 == 3 >= num_confirmations` → X executes, even though key `A` no longer exists on the account and only 2 live keys (`B`, `C`) actually approved it — request executed below the intended live-key threshold. [2](#0-1) [1](#0-0)

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
