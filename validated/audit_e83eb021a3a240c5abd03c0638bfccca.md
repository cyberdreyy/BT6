## Title
Removing a multisig member does not purge their existing confirmations on other pending requests, allowing a request to execute with fewer than `num_confirmations` live members - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
`delete_member` in `MultiSigContract` only removes requests that were *created by* the removed member and drops the confirmation records of those requests. It does **not** scrub the removed member's confirmation votes that are stored in `confirmations` for requests *created by other members*. Because `confirm` counts every entry present in the `confirmations` set regardless of whether the voter is still a current member, a request can reach `num_confirmations` and execute even though it was only actively approved by fewer than `num_confirmations` *currently trusted* members - one of the "votes" belongs to an account that has already been removed from the multisig.

### Finding Description
`confirm` only validates that the *calling* account is a current member via `assert_valid_request` → `current_member()`, but performs no re-validation of the members already recorded in the `confirmations: LookupMap<RequestId, HashSet<String>>` set for the request being confirmed: [1](#0-0) 

`delete_member` cleans up only the requests whose `member` field (the creator) equals the member being removed; it never inspects or prunes entries inside other requests' `confirmations` sets: [2](#0-1) 

`assert_valid_request` (called by `confirm`) also only checks that the *predecessor/signer of the current call* is a member - it never re-checks the historical entries already stored in the confirmations set: [3](#0-2) 

The same pattern exists in the older `multisig/src/lib.rs` contract: the `DeleteKey` action only removes requests created by the deleted key and clears their confirmations, but leaves that key's confirmation entries intact on requests created by other keys: [4](#0-3) [5](#0-4) 

The invariant that should hold is: `confirmations counted toward threshold == confirmations from currently live members`. This invariant is broken - a "ghost" confirmation from a removed member remains counted forever unless the specific request happens to also have been created by that same removed member.

### Impact Explanation
This lets a `MultiSigRequestAction` (e.g. `Transfer`, `FunctionCall`, `AddKey`/`AddMember`) execute with fewer live, currently-trusted approvals than `num_confirmations` mandates. For example, with `num_confirmations = 3` and members `{A, B, C, D}`: member `B`'s key is compromised or `B` otherwise becomes untrusted and is removed via `DeleteMember`. Any request that `B` had already confirmed before removal still shows `B` in its `confirmations` set. Subsequently only 2 of the remaining live members (say `C` and `D`) need to call `confirm` to push the count to 3 and trigger `execute_request`, even though only 2 currently-trusted members actually approved it. This is "a multisig request executed below threshold," matching the Critical impact category - funds can be transferred, keys/members added, or contracts deployed with an effectively lower approval bar than configured.

### Likelihood Explanation
This requires no special external attacker capability beyond normal multisig operation: any time a member is removed (for being compromised, off-boarded, etc.) while they had pending confirmations on other still-open requests, those confirmations silently persist and continue to count. Since member turnover (revoking a compromised or departing signer) is an expected, routine operation for BORG/DAO-style multisigs, the precondition is realistic and not contrived.

### Recommendation
When removing a member in `delete_member` (and `DeleteKey` in `multisig/src/lib.rs`), iterate over **all** open requests and strip the removed member's entry from every `confirmations` set (not just requests that member created). Alternatively, revalidate at `confirm`-time (and before `execute_request`) that every entry in the confirmations set still corresponds to a current member, discarding stale ones before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request` to create request `R` (e.g. `Transfer` to an attacker-controlled account).
3. `B` calls `confirm(R)` → `confirmations[R] = {B}` (only 1/3, not yet executed).
4. The multisig executes a separate, properly-approved `DeleteMember { member: B }` request (via `A`, `C`, `D` confirming) to remove `B` because their key was compromised. `delete_member` runs `self.confirmations.remove` only for requests created by `B`; `R` (created by `A`) is untouched, so `confirmations[R]` still equals `{B}`.
5. `C` calls `confirm(R)` → `confirmations[R].len() == 1`, `+1 == 2 < 3` → not yet executed, `confirmations[R] = {B, C}`.
6. `D` calls `confirm(R)` → `confirmations[R].len() == 2`, `+1 == 3 >= num_confirmations` → `execute_request` runs the `Transfer`, executing `R` with only `C` and `D` as live approving members plus the stale vote of removed member `B`.

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
