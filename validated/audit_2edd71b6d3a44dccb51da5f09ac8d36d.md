### Title
Confirmations from deleted multisig keys are not purged from pending requests, allowing execution below the live-member threshold - (File: multisig/src/lib.rs)

### Summary
The `DeleteKey` action in `execute_request` only purges requests **created by** the removed public key; it never removes that key's **confirmations on other, still-pending requests**. `confirm` then counts all entries in the stored `confirmations` set toward `num_confirmations` without checking whether each confirming key is still a live signer. As a result, a request can execute with fewer than `num_confirmations` currently-authorized keys, because a stale confirmation from a since-removed key is still counted.

### Finding Description
`add_request` stores a new `MultiSigRequestWithSigner` and an empty `HashSet<PublicKey>` of confirmations keyed by `request_id`. [1](#0-0) 

When a `DeleteKey` action executes, the contract removes only the *requests whose creator (`signer_pk`) equals the deleted key*, plus that key's `num_requests_pk` counter — it does not touch `self.confirmations` entries where the deleted key appears as a **confirmer** on a request it did not create: [2](#0-1) 

`confirm` simply checks the caller hasn't already confirmed and then compares the *size* of the stored confirmation set (+1) against `num_confirmations`, with no check that every member of that set is still a valid, live key: [3](#0-2) 

The invariant that should hold is:
`confirmations.len()` counted toward execution == number of **currently valid** signing keys that approved the request.

Once a key is deleted, this equality breaks for any request it previously confirmed (but did not create): the stale confirmation remains in the set and is still counted, even though that key is no longer entitled to authorize anything on the account.

The same pattern exists in `multisig2/src/lib.rs`, where `DeleteMember` similarly does not scrub that member's confirmations from other pending requests before `confirm` recomputes the threshold: [4](#0-3) 

### Impact Explanation
This lets a multisig request — including a `Transfer` of NEAR, an `AddKey`/`AddMember` (full-access key), or a `FunctionCall` — execute with approval from fewer live keys than `num_confirmations` requires. That is precisely the "multisig request executed below threshold" Critical scenario: funds can move, or a new full-access key can be added, when only one currently-valid key actually approved because a since-deleted key's stale confirmation still counts.

### Likelihood Explanation
This requires no attacker privilege beyond the normal lifecycle of multisig key rotation: any time a key is confirmed on a request and later removed (e.g., due to compromise, employee offboarding, or routine rotation) before that request is confirmed/executed or deleted, the stale confirmation persists and lowers the effective live-signer threshold for that specific pending request. Key rotation and pending, unconfirmed/undeleted requests are both realistic operational states for a multisig.

### Recommendation
When removing a key/member (`DeleteKey`/`DeleteMember`), also scan `self.confirmations` for all requests where the removed key/member appears as a confirmer (not just as request creator) and remove it from those sets, recomputing eligibility. Alternatively, `confirm` should validate that every entry in the stored confirmation set is still a current member/key before counting it toward `num_confirmations`.

### Proof of Concept
1. Multisig deployed with keys `A`, `B`, `C` and `num_confirmations = 2`.
2. `B` calls `add_request` (not `add_request_and_confirm`) with a `Transfer` action to an attacker-controlled account — confirmations set is empty.
3. `A` calls `confirm(request_id)` — confirmations = `{A}`, below threshold (1 < 2), request stays pending.
4. Separately, the multisig legitimately rotates keys and removes `A` via a `DeleteKey` request (e.g., because `A`'s key is suspected compromised). Per `execute_request`'s `DeleteKey` branch, only requests *created by* `A` are purged — the transfer request from step 2 (created by `B`) is untouched, and `A`'s stale confirmation remains in its `confirmations` set. [2](#0-1) 
5. `C` calls `confirm(request_id)` on the pending transfer request. `confirmations.len()` (1, from deleted key `A`) + 1 (`C`) = 2 ≥ `num_confirmations` (2), so `execute_request` runs the `Transfer`. [5](#0-4) 
6. The transfer executes with only one currently valid key (`C`) having actually approved it, even though the policy requires 2 live signers — funds move below the intended authorization threshold.

### Citations

**File:** multisig/src/lib.rs (L116-145)
```rust
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        assert_eq!(
            env::current_account_id(),
            env::predecessor_account_id(),
            "Predecessor account must much current account"
        );
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&env::signer_account_pk())
            .unwrap_or(0)
            + 1;
        assert!(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some."
        );
        self.num_requests_pk
            .insert(&env::signer_account_pk(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            signer_pk: env::signer_account_pk(),
            added_timestamp: env::block_timestamp(),
            request: request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
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

**File:** multisig/src/lib.rs (L246-261)
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
```

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```
