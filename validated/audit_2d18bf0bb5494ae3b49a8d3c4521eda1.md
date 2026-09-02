### Title
Stale confirmations from a deleted signer key allow a `MultiSigRequest` to execute below the real live-key threshold - (File: `multisig/src/lib.rs`)

### Summary
`MultiSigContract::execute_request`'s `DeleteKey` action only purges requests that were *created* by the deleted key, but never scrubs that key's public key out of the `confirmations` sets recorded against requests created by *other* signers. Because `confirm()` counts entries in that stale `HashSet<PublicKey>` toward `num_confirmations` without checking that each public key is still an active access key on the account, a request can reach the configured confirmation threshold while being backed by fewer live key-holders than required.

### Finding Description
`confirm()` reads the previously stored confirmation set and executes the request once `confirmations.len() as u32 + 1 >= self.num_confirmations`: [1](#0-0) 

The only place confirmations are ever invalidated due to key removal is inside the `DeleteKey` branch of `execute_request`, and it filters strictly on `r.signer_pk == pk` — i.e. requests whose *creator* was the deleted key: [2](#0-1) 

If public key `K1` had already called `confirm()` on a request `R` created by a different key `K2`, `K1`'s vote is stored in `self.confirmations[R]`. When `K1` is later removed from the account via a separate `DeleteKey { public_key: K1 }` request, the code only deletes requests where `K1` was the *signer_pk of the request itself* — it does not walk `self.confirmations` to strip `K1` out of confirmation sets for requests created by `K2` or anyone else. `R`'s confirmation set therefore still contains `K1` even though `K1` no longer holds access to the account.

This breaks the intended equality: `confirmations recorded for R == live key-holders who approved R`. After `K1` is deleted, the left side still counts `K1`, but the right side (actual live members who approved) is one fewer.

### Impact Explanation
`execute_request` can run arbitrary `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract` actions once the (stale) confirmation count reaches `num_confirmations`. With a stale confirmation counted, a request effectively executes with fewer live confirmations than the configured threshold — e.g. a 3-of-3 multisig can be satisfied by only 2 currently-authorized keys plus one already-revoked key's leftover vote. This is exactly the "multisig request executed below threshold" scenario, i.e. Critical impact: funds can move (`Transfer`), keys can be added, or contracts redeployed with less real authorization than the account owner configured.

### Likelihood Explanation
No foundation, owner, or victim cooperation is needed beyond the normal operation of the multisig account by its own current and former key-holders — this is intrinsic to how `MultiSigContract` processes `DeleteKey`. Any account that ever revokes a key that had outstanding confirmations on other pending requests is exposed; multisig setups that rotate signers (a common operational practice) are the most likely to trigger it, and the remaining key-holders can deliberately structure requests/confirmations to exploit this deterministically.

### Recommendation
When executing `DeleteKey`, iterate all `confirmations` entries (not only requests created by the deleted key) and remove the deleted public key from every confirmation set; alternatively, validate at `confirm()`/execution time that every public key still present in a request's confirmation set corresponds to a currently valid access key before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(3)` (3-of-3) with keys `K1`, `K2`, `K3`, `K4` attached as function-call access keys to the account.
2. `K2` calls `add_request` to create request `R` (e.g. `Transfer { amount }` to an attacker-controlled account).
3. `K1` calls `confirm(R)` → `confirmations[R] = {K1}` (1 of 3, request not yet executed).
4. Separately, `K3` and `K4` create and confirm a `DeleteKey { public_key: K1 }` request against the account (reaching 3-of-3 with `K2`, `K3`, `K4`, or however routed) to revoke `K1`'s access, e.g. because `K1`'s device was compromised or its holder left. Per `execute_request`'s `DeleteKey` branch, only requests where `signer_pk == K1` are purged; `confirmations[R]` still contains `K1`.
5. `K3` calls `confirm(R)` → `confirmations[R] = {K1, K3}` (2 of 3).
6. `K4` calls `confirm(R)` → `confirmations[R].len() + 1 >= 3` → `execute_request(R)` runs and the transfer executes, even though `K1` no longer has any access key on the account. Only two currently-live keys (`K3`, `K4`) actually approved, not the required three.

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
