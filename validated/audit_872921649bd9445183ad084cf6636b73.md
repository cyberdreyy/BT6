### Title
Stale confirmations from deleted multisig keys/members remain counted toward `num_confirmations`, allowing requests to execute below the intended live-signer threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
The multisig contracts track `confirmations` per pending `request_id` as a set of signer public keys/members. When a key (or member) is removed via `DeleteKey`/`DeleteMember`, the cleanup logic only purges requests that were *originally created* by the removed key (`r.signer_pk == pk` / `r.member == member`). It does not purge that key's *confirmation entries* on other still-pending requests that it merely confirmed but did not create. A stale confirmation from a now-removed key therefore continues to count toward `num_confirmations` for those other requests, letting them later execute with fewer genuinely live/authorized approvals than the threshold is supposed to guarantee.

### Finding Description
`confirm()` in the multisig contract simply compares `confirmations.len() + 1` against the cached `self.num_confirmations` value: [1](#0-0) 

The only cleanup of confirmation state tied to a removed key happens inside the `DeleteKey` action handler, and it is scoped to requests where the removed key was the *original signer* of the request (`r.signer_pk == pk`), not requests it merely confirmed: [2](#0-1) 

Consequently, if key `pk_B` confirms a request `R1` created by `pk_A` (adding its public key to `R1`'s confirmation set) but `R1` has not yet reached `num_confirmations`, and `pk_B`'s key is subsequently removed via a separate, properly-confirmed `DeleteKey` request, `R1`'s confirmation set still contains `pk_B`. Since the cleanup only scans for requests where `pk_B` is the *creator*, `R1` (created by `pk_A`) is untouched. A later confirmation by any remaining live key can push `confirmations.len() + 1` to `num_confirmations` and execute `R1`, counting `pk_B`'s stale, no-longer-authorized confirmation as if it were a live approval.

The equivalent bug exists in `multisig2`, where `delete_member` purges only requests filtered by `r.member == member` (the creator), leaving stale confirmation entries elsewhere untouched: [3](#0-2) [4](#0-3) 

The broken binding is: `confirmations counted for request R == confirmations from currently-live/authorized members for R`. After a key/member removal, this equality no longer holds for any request that removed member had confirmed (but not created), because the stale confirmation is never invalidated.

### Impact Explanation
This allows a multisig request to be executed while relying in part on an approval from a party that is no longer an authorized signer at execution time — effectively executing a request "below threshold" of genuinely live confirmations. Per the accepted impact categories, this is a Critical-class issue ("a multisig request executed below threshold"), since it can let a stale/removed key's earlier vote count toward moving funds, adding a full-access key, deploying code, or performing any other `MultiSigRequestAction` the multisig custodies.

### Likelihood Explanation
This requires no attacker privilege beyond being (or having been) a legitimate multisig member/key-holder — a realistic operational sequence: a member confirms one pending request, is later removed (e.g., key rotation, compromise response, member offboarding) through the normal, properly-authorized `DeleteKey`/`DeleteMember` flow, and the originally-confirmed request is executed afterward by the remaining live signers. No exploit of a privileged deployer/owner role is needed beyond the standard multisig confirmation flow itself, and no assumption that initialization was skipped is required — this is a logic gap in the ongoing request lifecycle, not a deployment misconfiguration.

### Recommendation
When a key/member is deleted, scan and purge that key/member's entry from the `confirmations` set of **all** pending requests (not just requests it created), e.g.:
```rust
for (request_id, mut confirmations) in self.confirmations.iter() {
    if confirmations.remove(&pk) {
        self.confirmations.insert(&request_id, &confirmations);
    }
}
```
Alternatively, revalidate at `confirm()`-time that every public key/member in the stored confirmation set is still a current, valid signer before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy multisig with 3 keys `pk_A`, `pk_B`, `pk_C` and `num_confirmations = 3`.
2. `pk_A` calls `add_request_and_confirm(R1)` (e.g., a `Transfer` action) → `confirmations(R1) = {pk_A}`.
3. `pk_B` calls `confirm(R1)` → `confirmations(R1) = {pk_A, pk_B}` (2 < 3, not executed).
4. All three keys confirm a separate request `R2 = DeleteKey(pk_B)` (properly reaching threshold 3) → executed via `multisig/src/lib.rs:198-216`; this only clears requests where `pk_B` was the creator, so `R1`'s confirmation set is untouched and still contains `pk_B`.
5. `pk_C` calls `confirm(R1)` → `confirmations(R1).len() + 1 == 3 >= num_confirmations` → `R1` executes (`multisig/src/lib.rs:246-266`), counting `pk_B`'s stale confirmation from a key that no longer exists on the account as one of the three required approvals.

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

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
