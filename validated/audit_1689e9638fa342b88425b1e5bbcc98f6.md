### Title
Stale confirmations from removed multisig keys/members are still counted toward `num_confirmations`, allowing requests to execute below threshold - ([File: multisig/src/lib.rs], [File: multisig2/src/lib.rs])

### Summary
Both `multisig/src/lib.rs` and `multisig2/src/lib.rs` let a signer/member confirm a request, then later be removed via `DeleteKey`/`DeleteMember`, without purging that signer's stale confirmation from *other* pending requests they had previously confirmed but did not originate. When those pending requests later reach `confirmations.len() + 1 >= num_confirmations`, they execute — even though one of the counted confirmations belongs to a key/member that is no longer part of the multisig. This breaks the multisig's core custody binding: `live confirming members >= num_confirmations` before executing privileged actions (transfers, key/member management, function calls).

### Finding Description
In `multisig/src/lib.rs`, `execute_request`'s `DeleteKey` branch only cleans up requests *originated* by the removed key: [1](#0-0) 

It filters `self.requests` where `r.signer_pk == pk` (the request's creator), and removes confirmations only for those requests. It never scans `self.confirmations` values to strip `pk` out of confirmation sets belonging to *other*, still-pending requests that `pk` had previously confirmed as a co-signer.

`confirm()` then blindly trusts the size of the stored confirmation set to decide whether to execute: [2](#0-1) 

There is no re-validation that every public key already present in `confirmations` for `request_id` is still a live access key on the account at the moment the threshold is reached.

The same defect exists in the rewritten `multisig2/src/lib.rs`. `delete_member` only removes requests where `r.member == member` (i.e. requests the removed member *originated*), leaving that member's confirmations on other pending requests untouched: [3](#0-2) 

And `confirm()` again just counts set size against `num_confirmations` without filtering for currently-valid members: [4](#0-3) 

### Impact Explanation
This directly matches the "multisig request executed below threshold" critical impact: a request can be executed with fewer than `num_confirmations` *live* confirmations, because one or more of the counted confirmations come from a public key/member that has since been removed from the multisig. Since `execute_request` can perform `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, etc., this allows moving NEAR or granting privileged access with an effective quorum below the configured threshold — undermining the entire purpose of the multisig's `num_confirmations` guarantee.

### Likelihood Explanation
This requires no special privilege beyond being an existing signer/member (which every legitimate multisig member already is): 
1. Member A creates request R and confirms it (1/`num_confirmations`).
2. Member B, who will later be removed, also confirms R (2/`num_confirmations`, still below threshold).
3. A separate legitimate `DeleteKey`/`DeleteMember` request removes B's key/membership from the multisig (satisfying its own quorum with other live members).
4. R's confirmation set still contains B's stale confirmation.
5. Member C confirms R, and `confirmations.len() + 1 >= num_confirmations` becomes true purely because B's stale confirmation is still counted, causing R to execute with only A and C as truly live confirmers.

This is a normal, foreseeable sequence of multisig operations (member rotation while requests are outstanding), not a contrived edge case, making the likelihood high in any multisig with active member turnover and concurrent pending requests.

### Recommendation
When removing a key (`DeleteKey`) or member (`DeleteMember`), iterate over *all* entries in `self.confirmations` (not just requests originated by that signer/member) and remove the deleted key/member from every confirmation set. Alternatively, when checking the threshold in `confirm()`, filter the stored confirmation set down to keys/members that are still valid before comparing its length against `num_confirmations`.

### Proof of Concept
1. Deploy multisig with `num_confirmations = 3` and members `{A, B, C, D}`.
2. A calls `add_request_and_confirm(R)` → confirmations(R) = `{A}`.
3. B calls `confirm(R)` → confirmations(R) = `{A, B}` (2/3, not yet executed).
4. Separately, `add_request_and_confirm` + confirmations from C and D remove B (`DeleteKey`/`DeleteMember` for B) — this executes normally since it only checks/clears requests originated by B, leaving R's confirmation set `{A, B}` untouched. B is now no longer a valid signer/member.
5. C calls `confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations`, so `execute_request(R)` runs, even though the “confirmations” backing it are really only from A and C (2 live members), not 3.

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
