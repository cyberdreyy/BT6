### Title
Stale confirmations from deleted keys let a multisig request execute below the true live-key threshold - (File: multisig/src/lib.rs)

### Summary
The `LenderCommitmentGroup_Smart` report describes an accounting value (`totalPrincipalTokensRepaid`) that can diverge from the real state it's supposed to track, corrupting a downstream comparison. The analogous flaw in `multisig/src/lib.rs` is that the `confirmations` set for a request can retain a public key that no longer has any authority on the account, so `confirm()`'s threshold check (`confirmations.len() as u32 + 1 >= self.num_confirmations`) can be satisfied by counting a "confirmation" from a key that has been deleted.

### Finding Description
`confirm()` checks the number of collected confirmations against `self.num_confirmations`: [1](#0-0) 

When a `DeleteKey` action is executed, the contract only removes **requests that were created by** the deleted key (`r.signer_pk == pk`), and clears the confirmation set for those specific requests. It never scans the `confirmations` map for other, still-pending requests to strip out entries belonging to the deleted key: [2](#0-1) 

Consequence: if key `A` confirms a request `R` created by a different key `D`, and afterward `A` is removed from the account via a separate `DeleteKey` request, `R`'s confirmation set still contains `A`'s public key. `R` was never touched by the `DeleteKey` cleanup logic because the filter only matches on request-creator (`signer_pk`), not on the members of `confirmations` sets across all requests.

Binding broken: the contract's k-of-n guarantee is `|{live keys that confirmed request R}| >= num_confirmations` before `R` executes. In reality, after key removal, the check becomes `|{keys that confirmed R, live or not}| >= num_confirmations`, i.e. confirmations counted diverges from live members backing the account.

### Impact Explanation
This lets a request reach the confirmation threshold and be executed (including `Transfer`, `AddKey`, `FunctionCall`, etc.) with fewer than `num_confirmations` distinct keys that are actually still valid access keys on the account. This is a multisig request executed below threshold — explicitly listed as a Critical impact in the rules (funds can move, or a rogue key/contract can be added, with fewer real approvals than the account's owners intended).

### Likelihood Explanation
This requires: (1) a request created by one key and confirmed (but not yet fully confirmed) by a second key, and (2) the second key subsequently deleted via a `DeleteKey` multisig action (e.g., routine key rotation, or revoking a compromised/departing signer) before the first request is deleted or fully confirmed/rejected. Multisig owners regularly rotate/removing keys as an operational security practice, and the 15-minute `REQUEST_COOLDOWN` before a request can be manually deleted (`delete_request`) creates a realistic window where a stale confirmation lingers. No attacker privilege beyond normal multisig key usage is needed; it is a latent state-consistency bug triggered by legitimate operational actions (key rotation), not requiring the foundation, owner override, or any out-of-scope condition.

### Recommendation
When executing `DeleteKey`, iterate over all entries in `self.confirmations` and remove the deleted public key from every confirmation set, not just from confirmations tied to requests it created. Alternatively, validate at `confirm()`-time (or execution time) that every public key in a request's confirmation set is still a valid access key on the account before counting it toward `num_confirmations`.

### Proof of Concept
1. Contract initialized with `num_confirmations = 3`; keys `A`, `B`, `C`, `D` are valid access keys on the account.
2. `D` calls `add_request` to create request `R` (e.g. `Transfer`). `confirmations[R] = {}`.
3. `A` calls `confirm(R)` → `confirmations[R] = {A}` (count 1 < 3, not executed).
4. `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (count 2 < 3, not executed).
5. Separately, the group decides to revoke key `A` (e.g. compromised device) and submits/confirms a `DeleteKey{public_key: A}` request. `execute_request`'s `DeleteKey` branch only removes requests where `signer_pk == A` — since `R` was created by `D`, it is untouched; `confirmations[R]` still equals `{A, B}`.
6. `C` calls `confirm(R)`: `confirmations.len() as u32 + 1 = 3 >= num_confirmations (3)` → `R` executes via `execute_request`, even though only `B` and `C` are live keys that approved it (`A`'s access key has been deleted) — i.e., the transfer executes with only 2 out of the required 3 live signer approvals. [1](#0-0) [2](#0-1)

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
