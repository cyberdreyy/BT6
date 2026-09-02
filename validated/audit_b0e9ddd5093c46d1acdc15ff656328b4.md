### Title
Multisig request can execute below the live-member confirmation threshold because removed members' stale confirmations remain counted - (File: multisig2/src/lib.rs)

### Summary
The multisig contracts (`multisig` and `multisig2`) count confirmations toward `num_confirmations` from a `HashSet` that is never purged of a member's votes when that member is later removed from the multisig (unless the removed member happens to be the *creator* of the request). This breaks the intended binding that "confirmations counted == confirmations from currently live/authorized members," letting a request execute using votes cast by accounts that are no longer members at execution time.

### Finding Description
`MultiSigContract::confirm` in [1](#0-0)  counts entries in `self.confirmations` (a `HashSet<String>` of member identities) against `self.num_confirmations` and executes the request once the count reaches threshold:

```rust
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
```

When a member is removed via `DeleteMember`, `delete_member` only purges requests **created by** the removed member, not confirmations that member cast on requests created by someone else: [2](#0-1) 

```rust
fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
    ...
    let request_ids: Vec<u32> = self
        .requests
        .iter()
        .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
        .collect();
    for request_id in request_ids {
        self.confirmations.remove(&request_id);
        self.requests.remove(&request_id);
    }
    ...
    self.members.remove(&member);
```

The filter is keyed on `r.member` (the requester who created the request), not on whether `member`'s identity appears inside the `confirmations` set of some *other* still-pending request. As a result, a stale confirmation from a now-removed member stays in `self.confirmations` for any request they didn't create, and is still added into the count when `confirm` is later called by remaining members.

The same root cause exists in the original `multisig` contract via `DeleteKey`, which only removes requests where `r.signer_pk == pk` (the requester's key), leaving that key's confirmations on other pending requests intact: [3](#0-2) , counted in `confirm` at [4](#0-3) .

This is the same class of bug as the referenced report: a value used to decide when a threshold/ordering condition is satisfied (`pack_key` for sort ordering there; the confirmation count here) is computed from stale/incorrect inputs, breaking the invariant the surrounding logic depends on (sorted-queue completeness there; K-of-N authorization here).

### Impact Explanation
This breaks the equality the multisig is supposed to enforce: `confirmations counted == confirmations from currently authorized/live members`. A request (e.g. a `Transfer` of the multisig's NEAR balance, or `AddKey`/`AddMember` granting new access) can be executed with fewer *live* member approvals than `num_confirmations` requires, because one or more of the counted confirmations belong to an account that has since been removed as a member. This is a "multisig request executed below threshold" scenario, which the impact taxonomy classifies as Critical (funds moved, or privileges granted, by a party not entitled to that level of authorization).

### Likelihood Explanation
This requires only a normal, expected multisig lifecycle sequence: a member confirms a request created by someone else, and is later removed from the multisig (a completely ordinary governance action — no compromised keys, no owner/foundation privilege abuse, no social engineering). Any multisig that ever removes a member while other requests are pending is exposed. No malicious deployment parameters or ignored initialization are needed.

### Recommendation
When removing a member (`DeleteMember`/`DeleteKey`), also scan `self.confirmations` (all pending requests, not just those the removed member created) and strip the removed member's identity from every confirmation set — or re-validate, at `confirm`/execution time, that every counted confirmer is still a current member before counting/executing.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm` to create request `R1` (e.g., `Transfer` of the account balance to an address `A` controls) — confirmations(`R1`) = `{A}`.
3. `B` calls `confirm(R1)` — confirmations(`R1`) = `{A, B}` (count 2, below threshold 3).
4. The group later legitimately removes `B` from the multisig via a separate `DeleteMember{member: B}` request confirmed by `A`, `C`, `D` — `delete_member` only purges requests where `r.member == B` (`B`'s own created requests); `R1` (created by `A`) is untouched, so confirmations(`R1`) still contains `B`.
5. `C` calls `confirm(R1)` — confirmations count becomes 3 (`A`, `B`, `C`) ≥ `num_confirmations` (3), so `R1` executes the `Transfer`, even though `B` is no longer a member and only 2 live members (`A`, `C`) actually authorized it at execution time. [5](#0-4) [1](#0-0)

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
