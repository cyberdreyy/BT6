### Title
Multisig `confirm` counts stale confirmations from removed keys/members, allowing request execution below the live-signer threshold - (File: `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` authorizes execution once the size of the stored confirmation set reaches `num_confirmations`, but the confirmation set is never purged of public keys that are later removed from the account via a legitimate `DeleteKey` request. A request created and partially confirmed by keys that are subsequently deleted still counts those stale confirmations toward the threshold, so a request can execute with fewer *currently valid* signers than the configured `K` of `K-of-N` scheme. The same class of bug exists in `multisig2` via `DeleteMember`.

### Finding Description
`confirm` only checks the cardinality of the confirmations `HashSet<PublicKey>` recorded for a request: [1](#0-0) 

When a `DeleteKey` action executes (itself just another multisig request that reached `num_confirmations`), the code only removes *requests that were created by* the deleted key (`r.signer_pk == pk`) and clears `num_requests_pk` for that key. It does **not** scan other pending requests' `confirmations` sets to strip out the now-deleted key's stale confirmation: [2](#0-1) 

As a result, if key `C` confirmed request `R1` (created by a different key `A`) and `C` is later removed from the account by a separate, properly-confirmed `DeleteKey{C}` request, `R1` retains `C`'s confirmation in its `confirmations` set even though `C` is no longer a valid access key on the account. `R1` can then be pushed over the `num_confirmations` threshold by fewer live keys than intended, because the stale confirmation from the deleted key is still counted as one of the `K` required signatures.

The `multisig2` variant has the analogous gap: `delete_member` only removes requests the member itself created (`r.member == member`) and clears `num_requests_pk`, but does not purge the member from `confirmations` on other pending requests: [3](#0-2) [4](#0-3) 

This breaks the intended custody binding: **confirmations counted (recorded in the `confirmations` map) versus live members (currently valid keys/accounts on the multisig)**. The contract's core guarantee — that any state-changing/fund-moving request requires `K` *currently authorized* signers — is violated once any signer set includes a removed key.

### Impact Explanation
This is Critical: it allows a multisig request (including `Transfer`, `AddKey`, `DeployContract`, etc.) to execute with fewer live/currently-authorized confirmations than `num_confirmations`, i.e., a multisig request executed below threshold. Funds can move, keys can be added, or the contract can be redeployed, having been "confirmed" only by `K-1` (or fewer) keys that are still valid at confirmation time.

### Likelihood Explanation
The trigger sequence uses only ordinary, expected multisig operations — no bug is required in the removal flow itself, only its incompleteness with respect to *other* pending requests:
1. Members create/partially confirm several requests over time (a normal admin/rotation pattern for any long-lived multisig).
2. The multisig legitimately rotates out a compromised or departing key via a normal `DeleteKey`/`DeleteMember` request.
3. Any older pending request that the removed key had previously confirmed retains that stale confirmation.
4. A later confirmation by a still-valid member completes the count and executes the request — with one fewer live signer than `K` actually agreeing at execution time.

Because key rotation with outstanding unconfirmed requests is a realistic operational scenario (not a contrived edge case), likelihood is meaningful, though it depends on timing (a pending request must exist when a confirming key is removed).

### Recommendation
- When executing `DeleteKey` (`multisig`) or `DeleteMember` (`multisig2`), iterate over **all** pending requests' `confirmations` sets (not just requests the removed key/member itself created) and remove the deleted key/member from each. If removal drops a request's live confirmation count, leave it pending.
- Alternatively/additionally, validate at `confirm`-time (and before executing) that every public key/member recorded in a request's confirmation set is still a current member/access key, discounting stale entries from the threshold count.

### Proof of Concept
1. Deploy multisig with `num_confirmations = 3` and keys `A, B, C, D` (4 valid keys).
2. Key `A` calls `add_request(R1)` (e.g., `Transfer` to some receiver). `R1` has 0 confirmations.
3. Key `B` calls `confirm(R1)` → 1 confirmation.
4. Key `C` calls `confirm(R1)` → 2 confirmations (`{B, C}`).
5. Separately, members legitimately create and confirm (via `A`, `B`, `D`) a `DeleteKey{public_key: C}` request that reaches 3 confirmations and executes, removing `C` from the account's access keys via `execute_request`/`MultiSigRequestAction::DeleteKey` at `multisig/src/lib.rs:198-216`. Note this handler only removes requests where `r.signer_pk == C` (requests created by `C`); `R1` (created by `A`) is untouched, so `R1.confirmations` still equals `{B, C}`.
6. Key `D` (the last remaining live key besides `A`/`B`) calls `confirm(R1)`. `confirmations.len() + 1 == 3 == num_confirmations`, so `R1` executes per `multisig/src/lib.rs:255-260`.
7. Result: `R1` (e.g., a `Transfer`) executes with confirmations `{B, C, D}`, but `C`'s key was deleted before `D`'s confirmation — only 2 currently-valid keys (`B`, `D`) actually agreed to `R1` at execution time, even though the contract enforces `K = 3`.

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
