### Title
Stale Confirmations From Deleted/Revoked Keys Are Not Purged From Other Pending Requests, Allowing A Request To Execute Below The Live-Signer Threshold - ([File: multisig/src/lib.rs])

### Summary
`MultiSigContract::execute_request`'s `DeleteKey` action only removes the requests that were *created* by the deleted public key; it never scrubs that key from the `confirmations` sets of *other* still-pending requests that the key had already confirmed. `confirm()` later counts these stale entries toward `num_confirmations` without checking that the confirming keys are still valid signers of the multisig.

### Finding Description
The intended invariant is: `confirmations_counted(request) == confirmations_from_currently_valid_keys(request)`. This is what determines whether `num_confirmations` (the k-of-n threshold) has genuinely been met.

`DeleteKey` handling in `execute_request` only cleans up requests where the deleted key is the *signer* of the request itself: [1](#0-0) 

It never iterates over `self.confirmations` to strip the deleted `public_key` out of the `HashSet<PublicKey>` stored for *other* requests that this key may have already confirmed via `confirm()`: [2](#0-1) 

`remove_request`, called both from `delete_request` and from `confirm` on threshold-reached, also never validates confirming keys against a "currently active keys" list — there isn't one; `MultiSigContract` has no `HashSet` of live public keys, only `num_requests_pk` and `confirmations` per request: [3](#0-2) [4](#0-3) 

Sequence breaking the binding:
1. Multisig configured with `num_confirmations = 2`, keys `A`, `B`, `C` are attached full-access keys.
2. Request `R2` (e.g. a `Transfer`) is created and confirmed once by `A`. `confirmations[R2] = {A}` (count 1, below threshold).
3. Separately, the group rotates `A` out via a `DeleteKey{public_key: A}` request, confirmed by `B` and `C` and executed. This removes `A`'s own pending requests and its `num_requests_pk` entry, but `confirmations[R2]` still contains `A`.
4. `B` confirms `R2`. `confirm()` computes `confirmations.len() as u32 + 1 >= self.num_confirmations` → `1 + 1 >= 2` → true, and executes `R2`.

At this point `confirmations_counted(R2) = 2` (`A`, `B`) but `confirmations_from_live_keys(R2) = 1` (`B` only, since `A`'s key was deleted in step 3). The equality the contract relies on to authorize execution is broken, and the request executes with only one genuinely live confirmation instead of the required two.

### Impact Explanation
This is a Critical-severity authorization bypass in the sense defined by the rules ("a multisig request executed below threshold"). A transfer, `AddKey`, `DeployContract`, or `FunctionCall` action can be pushed through with fewer live confirmations than the configured `num_confirmations`, effectively lowering the multisig's security threshold without anyone explicitly approving that change. No foundation, contract owner, victim key, or malicious node is required — it only needs normal, legitimate multisig operations (a pending unconfirmed request plus a routine key rotation) that any deployment following the documented flow would perform.

### Likelihood Explanation
Key rotation via `DeleteKey` and having more than one request outstanding at a time are both normal, documented usage patterns of this contract (the contract even limits "active requests per key" to 12, implying multiple simultaneous pending requests are expected). Any multisig group that revokes/rotates a signer while another request is mid-confirmation is exposed, without any attacker-controlled deployment misconfiguration.

### Recommendation
Maintain an explicit set of currently valid signer public keys in `MultiSigContract`. On `DeleteKey`, in addition to purging requests signed by that key, iterate `self.confirmations` and remove the deleted key from every request's confirmation set (or lazily filter `confirmations` against the live-key set inside `confirm()` before comparing against `num_confirmations`), e.g.:

```rust
MultiSigRequestAction::DeleteKey { public_key } => {
    self.assert_self_request(receiver_id.clone());
    let pk: PublicKey = public_key.into();
    // existing cleanup of requests signed by pk ...
    // NEW: purge pk from confirmations of all remaining requests
    let all_request_ids: Vec<u32> = self.requests.keys().collect();
    for rid in all_request_ids {
        if let Some(mut confs) = self.confirmations.get(&rid) {
            if confs.remove(&pk) {
                self.confirmations.insert(&rid, &confs);
            }
        }
    }
    self.num_requests_pk.remove(&pk);
    promise.delete_key(pk)
}
```

### Proof of Concept
1. `let mut c = MultiSigContract::new(2);` with three keys `A`, `B`, `C` already added as full-access keys.
2. As `A`: `c.add_request(Transfer{...})` → `request_id = R2`; then `c.confirm(R2)` (as `A`) → `confirmations[R2] = {A}`.
3. As `B` (or `C`): `c.add_request_and_confirm(DeleteKey{public_key: A})`, then confirm with the second key to reach threshold and execute — this deletes `A` on-chain and removes `A`'s own requests/`num_requests_pk`, but leaves `confirmations[R2] = {A}` untouched (per `execute_request`'s `DeleteKey` arm, lines 198-216).
4. As `B`: `c.confirm(R2)` → `confirmations.len() (1) + 1 >= num_confirmations (2)` is true → `R2` executes, even though only `B` is a currently valid key that approved it. [2](#0-1)

### Citations

**File:** multisig/src/lib.rs (L79-89)
```rust
#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize)]
pub struct MultiSigContract {
    num_confirmations: u32,
    request_nonce: RequestId,
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>,
    num_requests_pk: UnorderedMap<PublicKey, u32>,
    // per key
    active_requests_limit: u32,
}
```

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
