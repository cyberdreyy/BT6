## Analysis: Multisig confirmation counting is not revalidated against live members

The GitLab report describes a case where a permission check exists on one code path (design upload) but is silently bypassed by another (issue "Move to"), letting a low-privilege actor produce a state that should have required higher privilege. The closest analog in this repository is in the `multisig` (and `multisig2`) contracts: the k-of-n authorization threshold is enforced by counting entries in a `confirmations` set, but when a member's key/membership is revoked, only requests **created by** that member are purged — confirmations that member cast on **other, still-pending** requests are never invalidated. This lets a request execute with `num_confirmations` counted, while the actual number of currently-authorized (live) signers behind it is lower than the threshold.

### Root cause

`confirm()` counts set membership only, with no live-membership re-check at confirmation time: [1](#0-0) 

The only place stale confirmations are pruned is the `DeleteKey` action, and it filters by **request creator**, not by confirmer: [2](#0-1) 

Notice the filter is `r.signer_pk == pk` (the request's *original creator*), so a request created by member A that member B merely *confirmed* is left untouched when B's key is deleted — B's confirmation remains counted in `self.confirmations` for that request.

The same pattern exists in `multisig2`, where `delete_member` filters `r.member == member` (creator match) before pruning: [3](#0-2) 

and `confirm()` there likewise just checks set membership/size with no re-validation against current `self.members`: [4](#0-3) 

### Title
Stale confirmations from removed multisig members are still counted toward the execution threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
`confirm()` executes a request once `confirmations.len() + 1 >= num_confirmations`, but confirmations already recorded by a member are never invalidated when that member is later removed via `DeleteKey` (multisig) / `DeleteMember` (multisig2) — unless that removed member happens to be the *creator* of the pending request. Any request they merely *confirmed* keeps their stale confirmation in the set.

### Finding Description
The equality the multisig is supposed to enforce is: `confirmations counted == confirmations from currently-live members`. The code breaks this:

1. Member A creates request `X` via `add_request` (0 confirmations).
2. Member B confirms `X` → `confirmations[X] = {B}`.
3. Member C confirms `X` → `confirmations[X] = {B, C}` (2 of, say, 3 required).
4. Separately, the multisig executes a legitimate `DeleteKey`/`DeleteMember` request removing B (e.g., offboarding). The cleanup code only removes requests where `r.signer_pk == B` (or `r.member == B`) — i.e., requests *created* by B. Since `X` was created by A, it is untouched, and B's stale confirmation remains in `confirmations[X]`.
5. Member D (a remaining live member) confirms `X`. `confirmations[X].len() + 1 == 3 >= num_confirmations`, so `X` executes — counted as 3 confirmations (B, C, D) even though B no longer holds a valid key/membership and only 2 live members (C, D) actually authorized it.

This exactly matches the allowed custody-binding class "confirmations counted versus live members": the threshold check trusts stale set membership instead of verifying the confirming keys are still valid members at execution time.

### Impact Explanation
This allows a multisig request (e.g., a `Transfer` of NEAR held by the multisig account, an `AddKey`/`AddMember` granting new access, or a `FunctionCall` moving funds elsewhere) to execute with fewer live authorized confirmations than the configured `num_confirmations` threshold. This is a direct match for the Critical impact category "a multisig request executed below threshold."

### Likelihood Explanation
No attacker-controlled cryptographic bypass is needed — this occurs through the contract's own intended workflow (create request → partial confirm → unrelated member removal → further confirm). Any multisig that experiences normal membership churn while requests are pending (a routine, expected operational pattern, not a contrived edge case) is exposed. The bug requires no assumption that a deployment ignored documented initialization; it's inherent to `execute_request`'s `DeleteKey`/`DeleteMember` handling as implemented.

### Recommendation
On `DeleteKey` (multisig) / `DeleteMember` (multisig2), scan **all** pending requests' `confirmations` sets (not just requests created by the removed key/member) and strip the removed identity from every confirmation set, re-evaluating whether any request's live confirmation count still meets the threshold. Alternatively, revalidate confirmer identities against current live membership inside `confirm()` before counting toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig` with keys `[A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request` for `Transfer{amount, receiver}` targeting `X` → request id `X`.
3. `B` calls `confirm(X)` → `confirmations[X] = {B}`.
4. `C` calls `confirm(X)` → `confirmations[X] = {B, C}` (below threshold, request stays pending).
5. Members execute a separate, fully-confirmed request `Y` = `DeleteKey{public_key: B}` (offboarding B). Per `multisig/src/lib.rs:198-216`, only requests created by `B` are purged from `requests`/`confirmations`; `X` (created by `A`) is unaffected — `confirmations[X]` still contains `B`.
6. `D` calls `confirm(X)`. `confirmations[X].len() + 1 == 3 >= num_confirmations`, so `execute_request` fires the `Transfer`, moving funds out of the multisig account with only 2 currently-live confirmations (`C`, `D`) instead of the required 3.

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
