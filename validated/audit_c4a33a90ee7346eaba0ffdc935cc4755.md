This confirms the vulnerability. `assert_valid_request` at `multisig/src/lib.rs:293-310` checks only that the request exists and the predecessor matches the current account (i.e., the transaction was signed by *some* valid access key of the multisig account) — it does **not** re-validate that every previously-recorded confirmer in `self.confirmations` still corresponds to a live, non-revoked key.

### Title
Stale confirmations from revoked/deleted multisig keys are not purged from other pending requests, allowing execution below the intended confirmation threshold - (File: multisig/src/lib.rs)

### Summary
The `DeleteKey` action handler in `execute_request()` only purges confirmations/requests that were **originated** by the deleted key (`r.signer_pk == pk`). It never scans `self.confirmations` for entries where the deleted key appears as a **confirmer** on requests originated by other keys. As a result, a `confirm()` cast by a key that is later revoked remains permanently counted toward `num_confirmations` for any other pending request, letting that request execute later with fewer currently-authorized (live) signers than the configured threshold.

### Finding Description
`MultiSigContract` stores, per `request_id`, a `HashSet<PublicKey>` of confirmations [1](#0-0) . `confirm()` simply counts the size of this set against `num_confirmations` and executes once the threshold is met, without checking whether each confirming key is still active [2](#0-1) .

When a key is revoked via the self-request `MultiSigRequestAction::DeleteKey`, the cleanup logic filters `self.requests` for entries whose **originating** `signer_pk` equals the deleted key, removing only those requests and their confirmation sets. It does not iterate `self.confirmations` to strip the deleted key from confirmation sets of requests originated by other signers: [3](#0-2) 

The same pattern exists in `multisig2/src/lib.rs`'s `delete_member()`, which filters `self.requests` by `r.member == member` but likewise never scrubs that member from confirmation sets of requests created by others: [4](#0-3) 

`assert_valid_request()`, called from both `confirm()` and `delete_request()`, only verifies the request/confirmations entries exist and that the call is self-directed — it performs no liveness check on the keys already recorded in the confirmation set [5](#0-4) .

The binding this breaks: the contract's security model promises that any executed request has been approved by `num_confirmations` **currently authorized** keys/members (`|{live signers who confirmed}| >= num_confirmations`). Because stale confirmations from removed keys persist and still count, the actual guarantee degrades to `|{any signer, live or revoked, who confirmed}| >= num_confirmations`, which is a weaker binding.

### Impact Explanation
This is a "multisig request executed below threshold" scenario, explicitly categorized as Critical impact: a `Transfer`, `AddKey`, `FunctionCall`, or `SetNumConfirmations` request can be finalized and executed by promise even though the number of currently-authorized live keys backing it is one (or more) less than `num_confirmations`, because a ghost confirmation from an already-revoked key is silently counted. This can allow funds to move, keys to be added, or contract state to change with insufficient live authorization — directly undermining the K-of-N custody guarantee the contract is meant to enforce.

### Likelihood Explanation
Exploitation requires a realistic and common operational sequence: (1) a key confirms a pending request, (2) that key is later revoked via `DeleteKey`/`DeleteMember` (e.g., because the device was lost, an employee left, or as routine key rotation) while the request it confirmed is still pending, and (3) the request is later pushed to completion using the stale confirmation plus fewer live confirmations than `num_confirmations`. Since `DeleteKey`/`DeleteMember` is a normal, expected multisig operation and pending requests naturally persist across such rotations (no automatic invalidation exists), this is readily triggerable without any privileged bypass — it only requires normal use of the documented key-rotation workflow.

### Recommendation
When executing `DeleteKey` (multisig) or `DeleteMember` (multisig2), iterate over **all** entries in `self.confirmations` (not just requests originated by the removed key) and remove the deleted key/member from every confirmation set. Alternatively, validate at `confirm()`/execution time that each entry in the confirmation set corresponds to a currently valid key/member (e.g., by checking against an explicit "live keys" set as multisig2 does for members) before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig` with `num_confirmations = 2` and keys `[A, B, C]`.
2. Key `A` calls `add_request_and_confirm` to create request `R` (e.g., `Transfer`), which auto-confirms with `A`'s signature — `confirmations[R] = {A}`.
3. Members decide `A`'s device is compromised and submit/confirm a separate `DeleteKey { public_key: A }` self-request, which reaches threshold and executes: `execute_request` filters `self.requests` for `signer_pk == A` — this does not include `R` (since `R` was created by `A` itself in this case, but consider instead that `B` created `R` and `A` merely confirmed it via `confirm(R)`) — so `confirmations[R]` still contains `A`, and key `A` is deleted from the NEAR account.
4. Later, key `C` calls `confirm(R)`. `confirmations[R].len() + 1 = {A, C}.len() = 2 >= num_confirmations (2)`, so `execute_request(R)` runs and the transfer executes — even though `A`'s key was revoked in step 3 and only one currently-live key (`C`) actually confirmed alongside a phantom confirmation from a now-deleted key. [3](#0-2) [2](#0-1)

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

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```
