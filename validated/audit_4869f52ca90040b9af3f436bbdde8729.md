## Title
Stale confirmations from deleted multisig keys still count toward the confirmation threshold, allowing requests to execute with fewer live-key approvals than `num_confirmations` - (`multisig/src/lib.rs`)

### Summary
The multisig contract's `DeleteKey` action only purges pending *requests originated by* the deleted key, but never scrubs that key's public key out of the `confirmations` sets of *other* pending requests it had previously confirmed. As a result, a confirmation cast by a key that is later removed from the account continues to count toward the `num_confirmations` threshold of any request it confirmed before being deleted, letting a request execute with fewer currently-live keys than the configured K-of-N threshold requires.

### Finding Description
`confirm()` counts entries in the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` set for a request and executes it once the count reaches `self.num_confirmations`: [1](#0-0) 

When a `DeleteKey` action is executed, the contract removes only the requests whose *original signer* (`signer_pk`) matches the deleted key, then removes that key's `num_requests_pk` entry and deletes the access key from the account: [2](#0-1) 

It does not iterate over `self.confirmations` to strip the deleted key from confirmation sets of requests it had merely *confirmed* (but did not originate). The `MultiSigRequestWithSigner` struct only tracks the original `signer_pk`, so there is no bookkeeping that maps "which requests has this key confirmed" for cleanup purposes: [3](#0-2) 

The invariant the contract is supposed to enforce is:
```
confirmations counted on a request == confirmations from keys that are still valid/live access keys on the account
```
After a `DeleteKey` execution, this equality breaks for any other pending request that the deleted key had already confirmed: the stale confirmation remains counted even though the key no longer has any authority over the account.

### Impact Explanation
This allows a `Transfer`, `FunctionCall`, `AddKey`, or any other multisig action to execute with strictly fewer live approving keys than `num_confirmations` mandates, because one of the "confirmations" is a ghost entry from a revoked key. This directly matches the Critical impact category "a multisig request executed below threshold" — funds can be moved, keys added/removed, or contract code deployed with an effectively lower signing threshold than configured, undermining the core K-of-N security guarantee the contract advertises.

### Likelihood Explanation
No privileged action beyond normal multisig operation is required — it only needs the ordinary sequence of: a key confirming a request, that same key later being removed via a separate, legitimately-executed `DeleteKey` request (a routine key-rotation/offboarding operation), and then the original request being confirmed by the remaining live keys. This is a very plausible, even likely, operational sequence for any active multisig account (e.g. rotating a lost/compromised key while other requests are pending), making the likelihood high.

### Recommendation
When executing `DeleteKey` (and analogously `DeleteMember` in `multisig2`), iterate over all entries of `self.confirmations` and remove the deleted public key/member from every confirmation set, not just from requests it originated. Alternatively, validate at `confirm()`/execution time that every public key present in a request's confirmation set still corresponds to a currently valid access key/member before counting it toward `num_confirmations`.

### Proof of Concept
Setup: `MultiSigContract::new(num_confirmations = 3)` with 4 keys `pk1, pk2, pk3, pk4`.

1. `pk1` calls `add_request_and_confirm(R1 = Transfer{...})` → `confirmations[R1] = {pk1}`.
2. `pk2` calls `confirm(R1)` → `confirmations[R1] = {pk1, pk2}` (2 of 3 — not yet executed).
3. `pk3` calls `add_request_and_confirm(R2 = DeleteKey{pk2})`.
4. `pk1` and `pk4` call `confirm(R2)` → reaches 3 confirmations, `execute_request` runs `DeleteKey{pk2}`: this deletes `pk2`'s access key from the account and removes only requests where `signer_pk == pk2` (none, since `pk1` is `R1`'s signer) — `confirmations[R1]` is left untouched as `{pk1, pk2}`.
5. `pk4` calls `confirm(R1)` → `confirmations[R1].len() + 1 >= 3` is satisfied by `{pk1, pk2, pk4}`, so `R1` (the `Transfer`) executes.

Result: the transfer executed with confirmations counted as 3, but only `pk1` and `pk4` were live keys at execution time — `pk2`'s confirmation was cast by a key already deleted from the account. The K-of-3 threshold was effectively bypassed with 2 live approvers.

### Citations

**File:** multisig/src/lib.rs (L70-77)
```rust
// An internal request wrapped with the signer_pk and added timestamp to determine num_requests_pk and prevent against malicious key holder gas attacks
#[derive(Clone, PartialEq, BorshDeserialize, BorshSerialize, Serialize, Deserialize)]
#[serde(crate = "near_sdk::serde")]
pub struct MultiSigRequestWithSigner {
    request: MultiSigRequest,
    signer_pk: PublicKey,
    added_timestamp: u64,
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
