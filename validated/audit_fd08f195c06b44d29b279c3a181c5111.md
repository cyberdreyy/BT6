Confirmed. This is the key finding: in `multisig/src/lib.rs`, when `DeleteKey` is executed (lines 198-216), the contract only purges requests whose `signer_pk` equals the deleted key (`r.signer_pk == pk`) — i.e., requests *originated* by that key. It does **not** scan `self.confirmations` for entries where the deleted key merely *confirmed* a request added by someone else. That confirmation entry (a raw `PublicKey` in a `HashSet<PublicKey>`) remains stored against the still-open request.

`confirm()` (lines 248-266) only checks the *count* of the confirmations set (`confirmations.len() as u32 + 1 >= self.num_confirmations`) — it never re-validates that each already-recorded public key is still an active multisig key. This breaks the intended equality `confirmations counted == live members who approved`, since a revoked key's stale approval still counts toward the threshold indefinitely. [1](#0-0) [2](#0-1) 

### Title
Multisig executes requests below the live-member threshold because confirmations from deleted keys are never purged - (File: `multisig/src/lib.rs`)

### Summary
`confirm()` authorizes and executes a request once `confirmations.len() + 1 >= num_confirmations`, but the contract never removes a public key's confirmation from *other* members' pending requests when that key is deleted via `DeleteKey`. `DeleteKey` only cleans up requests that the deleted key itself *originated* (`r.signer_pk == pk`), leaving stale confirmations from revoked keys inside the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` map for requests added by other signers.

### Finding Description
The `MultiSigRequestAction::DeleteKey` handler in `execute_request` filters `self.requests` for entries where `r.signer_pk == pk` (the request's *creator* key) and removes those requests plus their confirmation sets: [1](#0-0) 

This does not cover the case where the deleted key was used only to *confirm* a request created by a different key. That request stays in `self.requests`, and its `confirmations` `HashSet<PublicKey>` still contains the now-deleted key.

`confirm()` never re-validates the members already in the set — it purely counts set size: [3](#0-2) 

There is no mechanism anywhere in the contract (no `is_valid_confirmation`, no re-check of `num_requests_pk` membership, no on-chain enumeration of current access keys — acknowledged as a known limitation in the README's "Gotchas" section, which only discusses the opposite failure mode of the contract getting *locked*) that revalidates stale confirmations against currently active keys.

### Impact Explanation
This crosses the "confirmations counted versus live members" custody boundary explicitly called out as an accepted class. Concretely: with `num_confirmations = K` and `N` live keys, suppose key A creates request R (0 confirmations from itself unless `add_request_and_confirm`), and keys A and B confirm it (2 of K, say K=3, so R is still pending). If member B is subsequently removed via a separate `DeleteKey` request (approved by other legitimate members for routine key rotation/offboarding), R's confirmation set still contains B's now-revoked key. Only one more live key (say C) needs to confirm to reach `2 + 1 = 3 >= num_confirmations`, and R (e.g., a `Transfer` of the multisig account's NEAR balance) executes — despite only 2 currently-valid members (A and C) having actually approved it post-removal. Funds move (or an `AddKey`/`FunctionCall` executes) with fewer *live* approvals than the configured threshold, i.e. a multisig request is executed below the intended live-signer threshold, matching the Critical impact category ("a multisig request executed below threshold").

### Likelihood Explanation
No attacker-privileged action is required beyond what is already an expected part of the operational lifecycle of any long-lived multisig: adding requests, confirming them partially, and later rotating/removing a key. Any pending, partially-confirmed request that survives a legitimate `DeleteKey` action silently retains the revoked signer's weight. This is a routine sequence of unprivileged, standard multisig operations (not requiring any owner/foundation trust beyond what the multisig's own members already have), and the bug is deterministic and always reachable whenever key rotation happens while an unrelated request is pending.

### Recommendation
When executing `DeleteKey`, iterate all entries in `self.confirmations` (not just `self.requests` filtered by `signer_pk`) and remove the deleted public key from every confirmation set, or equivalently, in `confirm()`, filter/reconcile the retrieved confirmation set against the current set of valid keys (e.g., by cross-referencing `num_requests_pk` or an explicit live-keys registry) before comparing its length to `num_confirmations`.

### Proof of Concept
1. Initialize multisig with `num_confirmations = 3` and keys `A, B, C, D`.
2. Key `A` calls `add_request` for a `Transfer` request `R1` (receiver: some external account, amount: contract balance). `R1` has 0 confirmations.
3. Key `A` calls `confirm(R1)` → 1 confirmation (`{A}`).
4. Key `B` calls `confirm(R1)` → 2 confirmations (`{A, B}`), still below threshold of 3, so `R1` stays pending.
5. Members legitimately create and confirm a separate request `R2` with action `DeleteKey { public_key: B }` (routine offboarding of `B`), which executes because 3 confirmations are gathered from `A, C, D`. `B`'s key is now revoked. `R1` is untouched by this because `execute_request`'s `DeleteKey` branch only removes requests where `r.signer_pk == B`, and `R1.signer_pk == A`.
6. Key `C` calls `confirm(R1)` → `confirmations.len() (2) + 1 >= 3` → `R1` executes, transferring funds, even though only `A` and `C` are currently valid signers who approved it (2 of 4 live keys), below the configured 3-of-N threshold. [1](#0-0) [3](#0-2)

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
