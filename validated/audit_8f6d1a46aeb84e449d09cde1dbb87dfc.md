### Title
Stale confirmations from removed multisig members count toward the confirmation threshold, allowing request execution below the configured `num_confirmations` - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
The multisig contracts (`multisig/src/lib.rs` and `multisig2/src/lib.rs`) never purge a member's *existing confirmations on other pending requests* when that member is removed. `confirm()` counts confirmations purely by set size (`confirmations.len() as u32 + 1 >= self.num_confirmations`) without re-validating that every recorded confirmer is still a current member. This breaks the invariant `confirmations counted == confirmations from live members`, letting a request execute with fewer genuinely authorized approvals than the configured threshold.

### Finding Description
When a member confirms a request without pushing it over the threshold, their identifier (public key or account) is stored in the `confirmations: LookupMap<RequestId, HashSet<...>>` map for that `request_id`: [1](#0-0) 

Members are removed via `DeleteMember` (`multisig2`) or `DeleteKey` (`multisig`), both dispatched only through `execute_request`: [2](#0-1) 

The removal helper `delete_member` (multisig2) only cleans up requests that the removed member itself *created* (`r.member == member`); it does not scan or clean the `confirmations` set of any *other* request that the removed member had previously confirmed: [3](#0-2) 

The v1 equivalent, `DeleteKey`, has the exact same gap — it only removes requests created by the deleted `signer_pk`, not confirmations that key left on other requests: [4](#0-3) 

Because `confirm()` in both versions performs a raw `HashSet` length check with no cross-check against `self.members` (or valid keys in v1), a confirmation left behind by a since-removed member is still counted as a legitimate vote when a later, still-current member confirms the same request: [5](#0-4) 

This is the same bug class as the external Magnetar report: a security-critical decision (approving an action) is made by trusting recorded state (`confirmations` set) instead of re-validating it against the actual live authorization set (`self.members`) at the moment of use.

### Impact Explanation
This lets a multisig request — including `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` — execute with fewer *currently valid* confirmations than `num_confirmations` requires. Per the rules, "a multisig request executed below threshold" is explicitly a Critical impact: it allows a coalition smaller than the configured K-of-N to move funds or take administrative actions (e.g., add a full-access key) out of the multisig account, directly compromising custody of the account's NEAR/assets.

### Likelihood Explanation
Likelihood is moderate-to-high in any long-lived multisig where membership changes over time (a very common operational pattern, e.g. onboarding/offboarding team members or rotating keys). No collusion with the foundation, an owner, or any privileged party beyond ordinary multisig members is required — an unprivileged confirming member's stale vote is silently retained and later exploited by any two remaining members timing their actions, which is well within normal usage of the contract's own exposed methods (`confirm`, `add_request`, `execute_request` via `DeleteMember`/`DeleteKey`). No redeploy, RPC interception, or social engineering is needed.

### Recommendation
When executing a `DeleteMember`/`DeleteKey` action, iterate over *all* active requests' `confirmations` sets (not just requests created by the removed member) and strip the removed member's identifier from each. Alternatively/additionally, at execution time in `confirm()`, filter `confirmations` to only those entries whose corresponding identifier is still present in `self.members` (v2) or still has a valid access key (v1) before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize a multisig with members `M1, M2, M3, M4` and `num_confirmations = 3`.
2. `M1` calls `add_request` to create request `A` (e.g., `Transfer` to an attacker-controlled account). No confirmation recorded yet.
3. `M2` calls `confirm(A)` → `confirmations = {M2}` (len 1, `1+1 < 3`, stored, not executed).
4. `M3` calls `confirm(A)` → `confirmations = {M2, M3}` (len 2, `2+1 < 3`, stored, not executed).
5. Separately, `M1` (or any member) creates and gets a `DeleteMember { member: M2 }` request fully confirmed and executed (via `execute_request` → `delete_member`) — see [3](#0-2) . This removes `M2` from `self.members`, but request `A`'s `confirmations` set still contains `M2`.
6. `M4` (still a valid member) calls `confirm(A)`. `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → request `A` executes via `execute_request`, even though only `M3` and `M4` are actually current, valid confirmers — one confirmation short of the real 3-of-4 requirement.

### Citations

**File:** multisig2/src/lib.rs (L239-242)
```rust
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
                }
```

**File:** multisig2/src/lib.rs (L299-315)
```rust
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
