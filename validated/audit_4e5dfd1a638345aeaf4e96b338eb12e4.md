### Title
Multisig executes requests below the intended confirmation threshold via stale confirmations from removed members - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` counts confirmations from a stored `HashSet` without re-validating that each confirming key/member is still a live member of the multisig at execution time. When a member/key is removed via `DeleteMember`/`DeleteKey`, the cleanup logic only purges requests and confirmations *originated* by that member — it does not scrub that member's stale confirmations from other, still-pending requests. Those stale confirmations remain counted toward `num_confirmations`, allowing a request to execute with fewer live-member signatures than the configured threshold.

### Finding Description
`confirm` in `multisig2/src/lib.rs` reaches execution once `confirmations.len() as u32 + 1 >= self.num_confirmations`: [1](#0-0) 

The `confirmations` set is a `HashSet<String>` keyed by request, accumulated over the lifetime of the request: [2](#0-1) 

When a member is removed via `DeleteMember`, `delete_member` only deletes requests that member *originated* (`r.member == member`), together with their confirmations. It does not touch confirmations that the removed member previously cast on *other* members' requests: [3](#0-2) 

`assert_valid_request`, which gates `confirm`/`delete_request`, only checks that the *current caller* is a live member and that the request/confirmation entries exist — it never revalidates the members recorded inside the stored `confirmations` set: [4](#0-3) 

The same design exists in the older `multisig/src/lib.rs`: `DeleteKey` purges only requests signed (originated) by the removed public key, leaving that key's confirmations on other pending requests intact, and `confirm` counts the raw confirmation set size the same way: [5](#0-4) [6](#0-5) 

**Binding broken:** the protocol's guarantee is `live confirmations at execution == num_confirmations` (K-of-N over currently authorized members). The actual invariant maintained is `total historical confirmation strings stored == threshold`, which can include confirmations from members no longer part of the set. This is the same root-cause pattern as the referenced report: a state-mutating action (removing a member) is not reconciled against dependent accounting (`confirmations`) that other code paths (`confirm`) rely on being consistent, so a downstream operation "trusts" data that is no longer valid for the current authorization set.

### Impact Explanation
This allows a multisig request (e.g., `Transfer`, `FunctionCall`, `AddKey`/`AddMember` granting full access) to execute with fewer live, currently-authorized confirmations than `num_confirmations` requires. Concretely: if member A confirms a pending request R (created by another member) and is later removed from the multisig by a separate, properly-confirmed `DeleteMember` action, A's confirmation on R survives. The remaining live members then only need `num_confirmations - 1` additional live confirmations to push R over threshold and execute it — effectively lowering the security threshold by one confirmation per stale/removed confirmer. Since these contracts custody NEAR and control full-access/function-call keys, this can result in a transfer or privileged action executing below the documented K-of-N threshold, i.e., a multisig request executed below threshold — Critical impact as defined.

### Likelihood Explanation
No privileged capability is needed beyond normal multisig operation: any member can create/confirm requests, and member removal is a standard governance action already supported by the contract's own API (`DeleteMember`/`DeleteKey`). The scenario requires ordinary usage patterns (a pending request outstanding while membership changes), which is plausible in any long-lived multisig with membership churn (e.g., replacing a compromised or departing signer) — it does not require a redeploy, victim key compromise, or any out-of-scope precondition.

### Recommendation
When executing `DeleteMember`/`DeleteKey`, iterate all pending requests' `confirmations` sets (not just those the member originated) and remove entries belonging to the removed member. Alternatively (and more robustly), at `confirm` time recompute the *live* confirmation count by intersecting the stored confirmation set with the current `members` set before comparing against `num_confirmations`, so stale confirmations from removed members never count toward the threshold.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. B calls `add_request` to create request `R` (e.g., `Transfer` of contract funds) — `confirmations[R] = {}`.
3. A calls `confirm(R)` — `confirmations[R] = {A}` (len 1, below threshold, not executed).
4. Separately, members reach 3-of-4 confirmations on a `DeleteMember{A}` request and it executes: `delete_member` removes A from `members`, and only deletes requests where `r.member == A` (requests A *created*) — `R` (created by B) and its confirmation set (still containing A) are untouched, per `multisig2/src/lib.rs:355-379`.
5. Now live members are `{B, C, D}`, threshold still 3.
6. C calls `confirm(R)` — `confirmations[R] = {A, C}` (len 2).
7. D calls `confirm(R)` — `confirmations[R] = {A, C, D}` (len 3) → `3 >= num_confirmations (3)` → `execute_request(R)` runs, per `multisig2/src/lib.rs:294-315`.
8. Result: `R` executes with only 2 live confirmations (C, D) plus a stale confirmation from removed member A, despite `num_confirmations = 3` intending 3 *live* member approvals.

### Citations

**File:** multisig2/src/lib.rs (L118-133)
```rust
pub struct MultiSigContract {
    /// Members of the multisig.
    members: UnorderedSet<MultisigMember>,
    /// Number of confirmations required.
    num_confirmations: u32,
    /// Latest request nonce.
    request_nonce: RequestId,
    /// All active requests.
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
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
