### Title
Multisig executes requests below the live-member confirmation threshold because stale confirmations from removed members are never purged - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

### Summary
The reported bug class (a state transition — plan completion — is finalized without correcting the dependent resource it guards) maps onto this repository's multisig contracts as: the `confirm()` threshold check counts confirmations from members who may no longer be members by the time the threshold is reached, because `delete_member`/`DeleteKey` only purges requests *created by* the removed member, not confirmations that member left on *other* still-pending requests.

### Finding Description
`MultiSigContract::confirm()` in `multisig2/src/lib.rs` decides whether to execute a request purely by comparing the size of the stored `confirmations: HashSet<String>` for that request against `num_confirmations`: [1](#0-0) 

Membership is only validated for the *caller* of `confirm` (via `assert_valid_request` → `current_member()`); the members who previously confirmed the same request are never re-validated against the current member set: [2](#0-1) 

When a member is removed, `delete_member` only cleans up requests that were *created by* that member — it does not scan or clean confirmation sets on *other* outstanding requests that the removed member had already confirmed: [3](#0-2) 

So the binding that should hold — `confirmations recorded for a request == confirmations from accounts that are still current members` — is broken. A confirmation from an account that has since been removed as a member remains counted toward the threshold indefinitely.

The same pattern exists in the legacy `multisig/src/lib.rs`: `DeleteKey` only removes requests where `r.signer_pk == pk` (the deleted key's own requests), leaving that key's confirmations on other pending requests intact: [4](#0-3) [5](#0-4) 

### Impact Explanation
This is Critical per the impact taxonomy: "a multisig request executed below threshold." An attacker (or a stale/compromised former signer) can get a `Transfer`/`FunctionCall`/`AddKey` request confirmed and executed by the contract even though the number of *currently valid* signers who approved it is strictly less than `num_confirmations`. Funds held by the multisig account can be moved by a party set that would not pass the configured M-of-N policy if membership were revalidated, i.e., NEAR is moved without the entitled level of authorization.

### Likelihood Explanation
This requires no special privilege beyond being (at some point) a member who confirms a request that is left pending while membership changes, and later having a remaining valid member push it over threshold. Membership changes (onboarding/offboarding signers) are a normal multisig lifecycle operation, so any request that stays open across a membership change is affected — this is a realistic, not merely theoretical, ordering of operations, entirely reachable by unprivileged sequencing of the contract's own public methods (`add_request`, `confirm`, `execute_request` via `DeleteMember`/`DeleteKey`).

### Recommendation
In `confirm()`, revalidate that every account/key recorded in the request's `confirmations` set (not just the caller) is still a current member before counting it toward `num_confirmations`. Alternatively, when a member is deleted, iterate all outstanding requests' confirmation sets (not only requests created by that member) and strip that member's confirmation from them, re-checking whether any request now falls back below or still meets the required threshold consistently.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `D` calls `add_request_and_confirm` with a `Transfer` request `R1` to an attacker-controlled account — `R1.confirmations = {D}`.
3. `B` calls `confirm(R1)` — `R1.confirmations = {D, B}` (2 < 3, not yet executed).
4. A separate request `R2 = DeleteMember { member: D }` is created and confirmed to threshold by `A, B, C`, executing and removing `D` from `members` (per `delete_member`, `multisig2/src/lib.rs:356-379`). `R1` is untouched because it was not *created by* `D`.
5. `C` calls `confirm(R1)`. `confirmations.len() (2, still containing stale D) + 1 = 3 >= num_confirmations (3)` → `execute_request(R1)` runs the `Transfer`, even though only `B` and `C` are actually current valid members who approved it — one fewer live confirmation than the policy requires.

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
