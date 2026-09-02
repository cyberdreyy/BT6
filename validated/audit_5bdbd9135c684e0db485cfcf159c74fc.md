### Title
Stale confirmations from deleted multisig keys are still counted toward `num_confirmations`, allowing a request to execute below the intended live-member threshold - (File: `multisig/src/lib.rs`)

### Summary
`multisig`'s `confirm()` only checks `confirmations.len() + 1 >= self.num_confirmations` without verifying that every public key already recorded in `confirmations` for a request is still a valid access key on the multisig account. When a `DeleteKey` request executes, the cleanup logic only purges *requests originally created* by the deleted key, not *confirmations that key previously cast on other, still-pending requests*. This lets a request execute using confirmations from keys that have since been removed, breaking the equality that "confirmations counted" must equal "confirmations from currently-live members."

### Finding Description
`confirm()` in [1](#0-0)  adds the signer's public key to a `confirmations` set for a `request_id` and, once `confirmations.len() + 1 >= self.num_confirmations`, executes the request via `execute_request`. There is no re-validation that keys already present in `confirmations` remain valid access keys.

The `DeleteKey` action in `execute_request` at [2](#0-1)  only removes requests whose **original creator** (`signer_pk`) equals the deleted key:
```
let request_ids: Vec<u32> = self.requests.iter()
    .filter(|(_k, r)| r.signer_pk == pk)
    .map(|(k, _r)| k).collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```
It never scans `self.confirmations` values (the sets of keys that *confirmed* each request) to strip out the deleted key from requests it merely confirmed but did not create. So if key `B` confirmed request `R` (created by key `A`), and `B`'s key is later deleted through a separate, successfully-executed `DeleteKey` multisig request, `R`'s confirmation set still contains `B`, even though `B` no longer exists as an access key on the account.

### Impact Explanation
This breaks the multisig's core custody guarantee — that a request executes only once `num_confirmations` *currently live* members have approved it. A stale confirmation from a deleted key still counts toward the threshold, so a request can be pushed through (transferring NEAR, deploying/upgrading contracts, or adding a new full-access key) with fewer than `num_confirmations` genuinely live approvals. Per the report's analog, this is the "confirmations counted versus live members" custody binding being violated, potentially enabling a multisig request (e.g. a `Transfer` action) to execute below the intended authorization threshold — a Critical-class outcome (funds moved, or authorization bypassed) per the rules.

### Likelihood Explanation
Exploitation requires: (1) a request created and partially confirmed by one signer, left pending; (2) a legitimate, threshold-approved `DeleteKey` action later removing that confirming signer's key (e.g., routine key rotation/offboarding); (3) the original pending request later collecting enough additional confirmations to reach `num_confirmations`, counting the stale one. This does not require a malicious owner/foundation/multisig-member action beyond normal key-rotation hygiene that the contract's own gotcha section already acknowledges is imperfect (it only warns about the total-key-count falling below threshold, not about stale confirmations surviving key deletion). Any member routinely rotating keys via `DeleteKey` while requests are in flight can trigger this without any special privilege beyond normal multisig operation.

### Recommendation
When executing a `DeleteKey` action, iterate over all entries of `self.confirmations` (not just `self.requests` filtered by `signer_pk`) and remove the deleted public key from every confirmation set; if this drops any request's live confirmations, treat it consistently (do not silently let it remain "confirmed"). Alternatively, revalidate at `confirm()` time that every key in the existing confirmation set is still a currently-valid access key on the account before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy multisig with `num_confirmations = 3` and keys `A, B, C, D`.
2. `A` calls `add_request` for `Transfer` action → request `R1` created (`signer_pk = A`), 0 confirmations.
3. `B` calls `confirm(R1)` → `confirmations[R1] = {B}` (1/3).
4. Separately, `A`, `C`, `D` create+confirm a `DeleteKey{public_key: B}` request that reaches threshold and executes, removing `B`'s access key from the account. The cleanup only removes requests created by `B` (there are none), so `confirmations[R1]` still contains `B`.
5. `C` calls `confirm(R1)` → `confirmations[R1].len() + 1 = 2 >= 3`? No — need one more. `D` calls `confirm(R1)` → now `len()+1 = 3 >= 3`, `R1` executes.
6. `R1` executed with confirmations `{B (stale/deleted), C, D}` — only 2 of the 3 counted approvers (`C`, `D`) are actually live keys at execution time, meaning the request executed with effectively fewer live confirmations than `num_confirmations` intended. [2](#0-1) [1](#0-0)

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
