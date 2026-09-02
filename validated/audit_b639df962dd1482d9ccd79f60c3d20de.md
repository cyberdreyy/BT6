### Title
Stale confirmations from deleted multisig keys let requests execute below the live-key threshold - (File: `multisig/src/lib.rs`)

### Summary
When a multisig key is removed via `DeleteKey`, the contract purges only the *requests originated by* that key. It never scans the `confirmations` map to strip that key's public key from confirmation sets of *other, still-pending requests* it had previously confirmed. Because `confirm()` counts raw confirmation-set size against `num_confirmations` without verifying that each confirming key is still a live access key, a stale confirmation from a removed key can combine with fewer live confirmations to cross the threshold and execute a request, breaking the binding "confirmations counted == live members who approved."

### Finding Description
`execute_request`'s `DeleteKey` branch only cleans up requests whose *original signer* (`r.signer_pk`) is the key being deleted, and removes that key's entry from `num_requests_pk`: [1](#0-0) 

It does not iterate `self.confirmations` to remove the deleted `public_key` from confirmation `HashSet`s belonging to *other* requests that key had already confirmed but that are still pending.

`confirm()` then decides whether to execute purely by comparing the size of the stored confirmation set to `num_confirmations`, with no re-validation that each entry in the set corresponds to a currently valid access key: [2](#0-1) 

The contract's own README documents that it has no way to query the blockchain for the current set of access keys, which is why this staleness can't be reconciled at confirm-time: [3](#0-2) 

As a result the equality the design relies on — `len(confirmations(request)) == number of currently-authorized keys that approved this request` — can diverge: a removed key's old confirmation continues to count as if it were a live approver.

### Impact Explanation
This is Critical per the "multisig request executed below threshold" category: a request (e.g. `Transfer`, `FunctionCall`, `AddKey` with a `FunctionCallPermission`) can be executed with fewer currently-live approving keys than `num_confirmations` requires, because a confirmation from an already-revoked key is still tallied. This directly breaks the authorization guarantee the multisig contract exists to enforce, and can move NEAR (`Transfer`) or grant access (`AddKey`) without the intended quorum of currently-trusted keys.

### Likelihood Explanation
This requires only ordinary, expected multisig operation, not any misconfiguration or foundation/owner shortcut: a key confirms a request, is later revoked through the contract's normal `DeleteKey` flow (e.g. because it was compromised, an employee left, or is being rotated), and the previously-partially-confirmed request is later completed by remaining keys. No special privilege beyond being one of the currently valid multisig keys is needed to trigger execution below threshold — one of them simply supplies the missing confirmation(s) and lets the stale one count.

### Recommendation
When executing `DeleteKey`, also scan `self.confirmations` for every pending request and remove the deleted `public_key` from each confirmation set (not just requests it authored). Alternatively, validate at `confirm()` time that all keys in a request's confirmation set are still valid access keys before counting them toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(2)` with keys `K1`, `K2`, `K3` (2-of-3).
2. `K1` calls `add_request` with a `Transfer` request `R` (receiver_id, amount) — not auto-confirmed.
3. `K2` calls `confirm(R)` → `confirmations(R) = {K2}` (1 < 2, so stored, not executed): see `confirm` logic at `multisig/src/lib.rs:248-266`.
4. Separately, a legitimate quorum executes a `DeleteKey { public_key: K2 }` request. `execute_request`'s `DeleteKey` branch (`multisig/src/lib.rs:198-216`) removes requests *authored* by `K2` and clears `num_requests_pk` for `K2`, but leaves `confirmations(R) = {K2}` untouched because `R` was authored by `K1`, not `K2`.
5. `K1` calls `confirm(R)`. `confirmations.len() + 1 == 2 >= num_confirmations(2)` succeeds, and `execute_request(R)` fires the `Transfer`, even though only one currently-live key (`K1`) ever approved `R` — the second "confirmation" came from a key that no longer exists on the account.


In `multisig/src/lib.rs`, inside `execute_request`'s `MultiSigRequestAction::DeleteKey` handling (around lines 198-216), extend the cleanup so that when a key is deleted, its public key is also removed from the `confirmations` `HashSet` of every other pending request (not just requests it originally authored). Concretely:

1. After computing `pk: PublicKey` for the key being deleted, iterate `self.requests` (all pending request IDs), and for each request ID, fetch its confirmation set from `self.confirmations`, remove `pk` from that set if present, and write the set back (or remove the request from `confirmations`/`requests` entirely if this causes issues with in-flight logic — the important invariant is that a removed key must never continue to count toward `num_confirmations`).
2. Keep the existing behavior of removing requests *authored by* the deleted key and clearing its `num_requests_pk` entry.
3. Add a test in `multisig/src/lib.rs` reproducing the PoC: create a 2-of-3 multisig, have K2 confirm a pending request (but not reach threshold), delete K2 via a separate DeleteKey request, then confirm the original pending request with K1 alone, and assert that the request is NOT executed (i.e., it still requires a second live key's confirmation) rather than executing with only K1's confirmation plus the stale K2 confirmation.
4. Verify no regression in existing tests (`test_multi_3_of_n`, `add_key_delete_key_storage_cleared`, etc.) in the same file.

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

**File:** multisig/README.md (L120-123)
```markdown
### Gotchas
 
User can delete access keys on the multisig such that total number of different access keys will fall below `num_confirmations`, rendering contract locked.
This is due to not having a way to query blockchain for current number of access keys on the account. See discussion here - https://github.com/nearprotocol/NEPs/issues/79.
```
