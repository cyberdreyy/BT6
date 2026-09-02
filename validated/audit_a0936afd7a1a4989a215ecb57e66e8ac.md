## Analysis

I found a valid analog to the "proposals do not have an expiration date" issue. The multisig contracts (`multisig` and `multisig2`) allow requests to sit indefinitely, and — more critically — when a member is removed, their *existing confirmations on other pending requests are never purged*. This lets a request be executed later using a confirmation from an account that is no longer a live member, effectively executing below the intended live-member threshold.

### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing a request to execute below the live-member threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
Both multisig implementations never expire pending requests, and when a member/key is deleted via `DeleteMember`/`DeleteKey`, only the *requests that member created* are purged — confirmations that removed member already cast on *other* still-pending requests are left untouched. Because `confirm()` only compares the size of the stored confirmation set against `num_confirmations`, a request can later be pushed over the threshold and executed even though one or more of the counted confirmations belong to accounts/keys that are no longer members of the multisig.

### Finding Description
In `multisig2/src/lib.rs`, `delete_member` removes only requests whose `member` field (i.e., the request's *creator*) equals the member being deleted: [1](#0-0) 

It does not scan `self.confirmations` for entries referencing the deleted member on requests created by someone else. `confirm()` simply checks the confirmation set's cardinality against `num_confirmations`, with no re-validation that every confirming member is still in `self.members`: [2](#0-1) 

The same pattern exists in the legacy `multisig/src/lib.rs`: `DeleteKey` only removes requests filtered by `r.signer_pk == pk` (the request creator), leaving that key's confirmations on other requests intact: [3](#0-2) 
and `confirm()` there likewise only checks confirmation-set size: [4](#0-3) 

Because requests have no expiration/created-timestamp validity window either, a request can be created, partially confirmed, left dormant while membership changes (including removal of a confirmer), and then pushed to execution far later using the stale confirmation.

**Binding broken:** `confirmations_counted == confirmations_from_live_members`. After a member is removed, the left side still includes that member's prior confirmation, while the right side (live members) does not — the equality no longer holds, yet `confirm()` treats the stale count as valid.

### Impact Explanation
This falls under the explicitly listed Critical impact bucket: "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` action can be executed with effectively fewer live-member approvals than `num_confirmations` requires, since one of the counted confirmations belongs to an account that has since been removed (e.g., for cause, such as key compromise or offboarding). This directly undermines the custody/authorization guarantee the K-of-N scheme is meant to provide over NEAR held by the multisig account.

### Likelihood Explanation
This requires only normal multisig operations that already occur in practice: a request is created, gets partial confirmations, and later a confirming member is removed (a routine operational event, e.g. rotating out a compromised or departing member). No privileged bypass, redeploy, or malicious validator is needed — any remaining member(s) can simply confirm the still-pending request to complete execution using the stale confirmation, whether accidentally or deliberately.

### Recommendation
- Add an expiration/staleness window to requests (using the existing `added_timestamp`) so that a request older than a defined TTL cannot be confirmed/executed and must be recreated.
- When removing a member (`delete_member`/`DeleteKey`), scan all pending requests' confirmation sets and strip the removed member's entry from every request, not just requests they created.
- Alternatively, revalidate at `confirm()`-time that every confirming identity in the stored set is still present in `self.members` (or the current access-key set) before counting it toward the threshold.

### Proof of Concept
1. Initialize `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request(R)` creating request `R` (e.g. `Transfer { amount }`) — `R.member = A`.
3. `B` calls `confirm(R)` → confirmations = `{B}`.
4. `C` calls `confirm(R)` → confirmations = `{B, C}` (still short of 3, not executed): see `confirm()` logic in `multisig2/src/lib.rs` (`confirmations.len() + 1 >= num_confirmations`).
5. Members execute a separate fully-confirmed request that performs `DeleteMember { member: C }`. `delete_member` only removes requests where `r.member == C` (i.e., requests *created* by `C`) — `R` was created by `A`, so it is untouched, and `C`'s confirmation on `R` remains in `self.confirmations[R]`. [5](#0-4) 
6. Later (no expiration exists), `D` calls `confirm(R)` → confirmations set size becomes 3 (`B`, `C`, `D`) which is `>= num_confirmations (3)`, so `execute_request(R)` runs and the transfer executes.
7. At execution time, only `B` and `D` are live members who actually confirmed; `C`'s confirmation is stale from a removed member. The request executed with only 2 live-member approvals against a nominal 3-of-N threshold. [2](#0-1)

### Citations

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
