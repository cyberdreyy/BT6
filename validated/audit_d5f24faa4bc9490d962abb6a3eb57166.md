### Title
Stale confirmations from a removed key allow multisig requests to execute below the live-key confirmation threshold - (File: `multisig/src/lib.rs`)

### Summary
The class of bug in the reference report (an operation that computes/returns a result relying on data that was never properly synchronized) has a direct analog in `multisig/src/lib.rs`: when a key is removed via `DeleteKey`, only the requests *originated* by that key are purged along with their confirmations. Any confirmation the removed key previously cast on requests it did **not** originate remains in the `confirmations` set and continues to count toward the `num_confirmations` threshold, even though the key can no longer sign anything on-chain. This breaks the intended binding: `confirmations.len()` (live, currently-valid signer approvals) should always equal the number of distinct still-authorized keys that approved a request, but it can retain phantom approvals from revoked keys.

### Finding Description
`MultiSigContract::confirm` decides whether to execute a request purely by counting entries in the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` set: [1](#0-0) 

When a `DeleteKey` action is executed, the code removes outstanding *requests originated by* the deleted key, and the `num_requests_pk` counter for it, but it never scans other pending requests' `confirmations` sets to strip the deleted key's already-cast confirmations from requests it did not originate: [2](#0-1) 

Compare this with `multisig2/src/lib.rs`'s `delete_member`, which was hardened with an assertion that `members.len()` stays `>= num_confirmations`, showing the developers were aware member/key removal interacts with the confirmation threshold — but that fix only guards against *reducing total membership below the threshold*, not against *stale confirmations from an already-removed key remaining counted* on `multisig/src/lib.rs` (v1), which has no such handling at all.

The invariant that should hold is:
```
confirmations_counted(request) == live_signing_keys_that_confirmed(request)
```
After a `DeleteKey` action removes key `K` (who confirmed request `R` but didn't originate it), the invariant is violated: `confirmations_counted(R)` still includes `K`, but `K` is no longer a live signer.

### Impact Explanation
This is a threshold-bypass on the multisig's core security guarantee (K-of-N). If `K` out of `N` keys are required, and one of the `N` keys was removed (e.g., because it was compromised or the holder left) after having confirmed a pending `Transfer`, `DeployContract`, `AddKey`, or `FunctionCall` request, that request can still reach execution with only `K-1` *live* signer approvals plus the stale, now-invalid confirmation — i.e., "a multisig request executed below threshold" per the accepted impact categories. This can move funds (`Transfer`), deploy arbitrary code (`DeployContract`/upgrade), or add unauthorized keys (`AddKey`) with fewer genuinely-authorized approvals than the contract's own security parameter dictates.

### Likelihood Explanation
This does not require any external/social-engineering step or a redeploy: it happens through the contract's own, documented `DeleteKey` action, which is a normal governance operation (e.g., rotating out a compromised or departing key holder). Any request that was confirmed-but-not-executed by the removed key before its removal becomes a live attack surface for as long as it sits in `requests`/`confirmations`. Given `REQUEST_COOLDOWN` and the `active_requests_limit` of 12, an attacker (or a leftover key from a legitimate rotation) has a realistic window to have pre-confirmed multiple pending requests before removal.

### Recommendation
When executing `MultiSigRequestAction::DeleteKey`, iterate over **all** pending requests' confirmation sets (not only those `signer_pk == pk`) and remove the deleted public key from each `HashSet<PublicKey>` in `self.confirmations`, so that any request relying on that stale confirmation must be re-confirmed by a currently live key to reach `num_confirmations`.

### Proof of Concept
1. Deploy `multisig` with `num_confirmations = 2` and keys `A`, `B`, `C`.
2. `A` calls `add_request_and_confirm` for a `Transfer` request `R1` (confirmations = {A}).
3. `C` calls `confirm(R1)` too but a separate action first raises `num_confirmations` to 3 via a `SetNumConfirmations` request confirmed by A,B,C so R1 isn't yet executed; instead assume simpler path: `num_confirmations = 3`, A creates `R1` (confirmations={A}), B confirms (confirmations={A,B}), still below 3.
4. Multisig owners now legitimately execute a `DeleteKey` request removing key `B` (e.g., because `B`'s device was lost) — this succeeds since it only checks requests originated by `B`; `R1`, originated by `A` with `B`'s confirmation, is untouched, so `confirmations(R1)` still contains `B`.
5. Now only `C` needs to call `confirm(R1)`: `confirmations.len() + 1 = 3 >= num_confirmations (3)` — `R1` (the `Transfer`) executes, even though the required 3rd live-key approval (from a currently valid key) never actually happened; `B`'s stale confirmation counted in its place. [2](#0-1) [3](#0-2)

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
