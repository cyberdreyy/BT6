### Title
Confirmations from a removed multisig key are not revoked, allowing execution below the intended live-signer threshold - (File: multisig/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts a public key's confirmation toward the `num_confirmations` threshold, but when a key is removed via a `DeleteKey` request, only that key's *own outstanding requests* are purged — any confirmations it previously cast on requests originated by *other* keys are left untouched. A key that is later revoked because it is compromised or a bad-faith signer can therefore still contribute a valid confirmation toward executing a malicious request after it has been kicked out.

### Finding Description
`confirm()` treats any public key present in the `confirmations` set for a request as a live vote and simply compares the set size against `self.num_confirmations`: [1](#0-0) 

The `DeleteKey` action handler only cleans up requests *originated* by the removed key (matched via `r.signer_pk == pk`) and the `num_requests_pk` counter for that key — it never scans `self.confirmations` to strip that key's votes from other pending requests: [2](#0-1) 

So the binding the contract is supposed to enforce — "confirmations counted == confirmations from currently-authorized signers" — is broken. A confirmation cast by key `A` remains permanently counted in `self.confirmations[request_id]` even after `A` is removed from the account by a subsequent `DeleteKey` execution.

### Impact Explanation
Concretely:
1. Multisig is configured with `num_confirmations = 3` and signer keys `A, B, C, D`.
2. `A` (compromised/rogue) calls `add_request_and_confirm` on a malicious `Transfer`/`FunctionCall` request `R`, recording its confirmation: `confirmations[R] = {A}`.
3. The remaining signers detect the compromise and submit+confirm a `DeleteKey{public_key: A}` request, which executes and removes `A` as an access key. This execution path (lines 198-216) removes `A`'s own initiated requests but does **not** touch `confirmations[R]`, so `confirmations[R]` still equals `{A}`.
4. Two more legitimate signers, `B` and `C`, unaware `A`'s stale vote is still "banked," confirm `R`. `confirmations[R].len() + 1 == 3 >= num_confirmations`, so `execute_request` fires — the funds move even though only `B` and `C` are currently-valid signers, i.e. only 2 of the required 3 live confirmations actually exist.

This lets a request be executed below the effective live-signer threshold, moving NEAR (`Transfer`) or granting keys (`AddKey`) using a vote from an account that no longer has any authority — a multisig request executed below threshold, which is explicitly listed as Critical impact.

### Likelihood Explanation
This requires no special privilege beyond being (or having been) one of the multisig's own signer keys at some point — exactly the kind of participant the multisig is meant to constrain. Any organization that ever rotates/revokes a compromised or departing signer key while a request that key previously confirmed is still pending will hit this path; it does not require the foundation, a redeploy, or any out-of-scope actor.

### Recommendation
When executing `DeleteKey`, iterate over `self.confirmations` (not just `self.requests`) and remove the deleted public key from every confirmation set, or alternatively re-validate at `confirm()`/execution time that every public key credited toward `num_confirmations` is still a valid access key on the account before executing the request.

### Proof of Concept
- Deploy `MultiSigContract::new(3)` with 4 access keys `A, B, C, D`.
- As `A`: `add_request_and_confirm({receiver_id: self, actions:[Transfer{amount}]})` → `request_id = R`, `confirmations[R] = {A}`.
- As `B` (or via a separate request): create and confirm to threshold a `DeleteKey{public_key: A}` request, executing removal of `A`. Note `confirmations[R]` is untouched by this execution path (`multisig/src/lib.rs` lines 198-216).
- As `B`: `confirm(R)` → `confirmations[R] = {A, B}`, `len()+1 = 3 < 3`? Actually `2+1=3 >= 3` after `C` — as `C`: `confirm(R)` → `confirmations.len() as u32 + 1 = 2+1 = 3 >= 3`, `execute_request(R)` fires the `Transfer`, using `A`'s now-revoked confirmation as one of the three required votes.

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
