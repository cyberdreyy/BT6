## Title
Stale confirmations from a deleted key are not purged, allowing a multisig request to execute below the live-signer threshold - (File: `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` counts entries in the per-request `confirmations: HashSet<PublicKey>` against `num_confirmations` without ever verifying that the public keys in that set still correspond to keys currently authorized on the account. When a key is removed via `MultiSigRequestAction::DeleteKey`, the contract only purges the requests that key itself *authored* (`r.signer_pk == pk`) and the removed key's `num_requests_pk` counter — it does **not** purge that key's confirmation entries on other, still-pending requests authored by different signers. Those stale confirmations remain counted forever, so a request can later reach `num_confirmations` and execute using fewer live-key approvals than the threshold requires.

### Finding Description
The custody binding that should hold is:
```
confirmations counted for request R == confirmations by keys that are currently live signers on the account
```
`confirm()` enforces only a raw count: [1](#0-0) 

`assert_valid_request()` never checks that the recorded confirming public keys are still valid access keys — it only checks that the request and its confirmation set exist: [2](#0-1) 

The `DeleteKey` action cleans up only requests *authored by* the removed key, leaving confirmations by that key on requests authored by others untouched: [3](#0-2) 

This is the direct structural analog of the reported bug: `pendingEmission` capped its result against `maxSupply`, but the sibling function `pendingEmissionPerSecond` omitted the equivalent cap, letting a value that should be bounded slip through uncapped. Here, `DeleteKey`'s cleanup logic removes stale state for requests authored by the deleted key, but the sibling cleanup for confirmations made by that key on *other* requests is missing — the "confirmations counted" value silently diverges from "confirmations by keys currently entitled to confirm."

### Impact Explanation
This falls under the explicitly in-scope Critical impact: "a multisig request executed below threshold." A `Transfer`, `AddKey`, `FunctionCall`, etc. request can be executed on behalf of the contract's account with fewer genuinely live approvals than `num_confirmations` mandates, because one or more counted confirmations belong to a key that has already been deleted from the account and can no longer sign anything. This breaks the fundamental multisig custody guarantee (k-of-n) without requiring any privileged action beyond the normal, expected `DeleteKey` request flow.

### Likelihood Explanation
This requires no attacker-controlled malicious input beyond normal contract usage: any account that rotates/removes a signing key (a documented, supported flow via `DeleteKey`) while that key has an outstanding confirmation on some other pending request will leave the vulnerable state. Any of the remaining valid key holders can then complete that request later with one fewer *live* confirmation than intended. No redeploy, foundation action, or social engineering is needed — only the ordinary sequence: confirm request A (by key K), then remove key K via a separate `DeleteKey` request, then complete request A with the remaining keys.

### Recommendation
When executing `DeleteKey`, also scan `self.confirmations` for all requests and remove the deleted public key from every confirmation set (not just remove requests authored by that key), e.g.:
```rust
let request_ids_to_clean: Vec<u32> = self.confirmations.keys().collect();
for request_id in request_ids_to_clean {
    if let Some(mut confs) = self.confirmations.get(&request_id) {
        if confs.remove(&pk) {
            self.confirmations.insert(&request_id, &confs);
        }
    }
}
```
Alternatively, verify at `confirm()`/execution time that every public key present in the confirmation set is still installed on the account (e.g., via a runtime host function) before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(3)` with three live full-access keys `A`, `B`, `C` on the account.
2. Key `B` calls `add_request` to create request `R1` (e.g. `Transfer`). `R1.signer_pk == B`.
3. Key `A` calls `confirm(R1)` → `confirmations[R1] = {A}`.
4. Key `C` calls `confirm(R1)` → `confirmations[R1] = {A, C}` (2 confirmations, still below 3, not executed).
5. Separately, a `DeleteKey { public_key: A }` request is created and confirmed by 3 keys (`A`, `B`, `C`) and executes: per `execute_request`, only requests where `r.signer_pk == A` are purged — `R1` (authored by `B`) is untouched, so `confirmations[R1]` still equals `{A, C}`. Key `A` is now deleted from the account.
6. Key `B` (the only remaining un-confirmed live key) calls `confirm(R1)`. In `confirm`, `confirmations.len() == 2`, `2 + 1 >= 3` → the request executes, even though only `B` and `C` are currently live signers — `A`'s confirmation is a phantom left over from a deleted key. The `Transfer` (or any bundled action) executes with 2 live approvals instead of the required 3.

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

**File:** multisig/src/lib.rs (L292-310)
```rust
    // Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert_eq!(
            env::current_account_id(),
            env::predecessor_account_id(),
            "Predecessor account must much current account"
        );
        // request must exist
        assert!(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed"
        );
        // request must have
        assert!(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests"
        );
    }
```
