### Title
Stale confirmations from removed multisig members still count toward execution threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` removes a member from `self.members` and purges only the *requests originated by* that member, but never scans the `confirmations` map to strip that member's prior votes from requests they merely *confirmed*. `confirm()` then tallies `confirmations.len()` without re-checking that each entry still belongs to a current member, so a removed member's stale confirmation continues to count toward `num_confirmations`, letting a request execute with fewer currently-trusted confirmers than the configured threshold.

### Finding Description
`delete_member` only cleans up requests where `r.member == member` (the requests that member itself created): [1](#0-0) 

It never iterates `self.confirmations` to remove the departing member's identity from confirmation sets on *other* still-pending requests that member had already confirmed. Meanwhile, `confirm()` decides whether to execute purely by counting set size: [2](#0-1) 

The binding that should hold is:
`count({m ∈ confirmations(request) : m ∈ current_members}) >= num_confirmations`

But the code actually evaluates:
`count(confirmations(request)) >= num_confirmations`

These two are not equal once a confirming member is removed after having cast a vote on a request that has not yet reached quorum. The stale vote is never invalidated, so it silently substitutes for a genuine, currently-trusted confirmer.

`current_member()` is only used to gate who may *call* `add_request`/`confirm`/`delete_request`, not to re-validate the historical contents of the `confirmations` set: [3](#0-2) 

### Impact Explanation
This directly breaks the "confirmations counted versus live members" custody binding called out in scope, and matches the Critical impact category "a multisig request executed below threshold." Any pending request (including `Transfer`, `DeployContract`, `AddKey`, `AddMember`/`DeleteMember`, or `FunctionCall`) that received a partial confirmation from a member who is later removed (e.g., because their key was compromised or they were dismissed) can still be pushed to execution using that removed member's leftover vote plus fewer than the intended number of live confirmations. This can result in unauthorized transfer of NEAR held by the multisig account, or unauthorized privileged actions (key/member changes, contract redeployment) being approved by a quorum that no longer reflects actual live members.

### Likelihood Explanation
The precondition is realistic and common: a multisig removing a member (compromised key, offboarding, governance change) is a routine operation, and it is very plausible that member had already confirmed one or more still-open requests before removal. No special privilege beyond normal multisig operation is required to trigger the divergence — the bug is a missing invalidation step, not a step requiring an external attacker at all, only a benign administrative action to remove a member.

### Recommendation
When `delete_member` runs, iterate all pending `requests`/`confirmations` and remove the departing member's identity string from every confirmation `HashSet`, not just delete requests they originated. Alternatively, re-validate confirming members at execution time in `confirm()` by intersecting the confirmation set with `self.members` before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `X` (e.g. `Transfer` of the account's NEAR balance).
3. `B` calls `confirm(X)` → `confirmations(X) = {B}`, size 1 (< 3, not executed).
4. Multisig executes a separate approved `DeleteMember { member: B }` request (per [4](#0-3) , delegating to `delete_member` at lines 356-379) because `B`'s key was compromised. This removes `B` from `self.members`, but `confirmations(X)` still contains `B`.
5. `C` calls `confirm(X)` → size 2. `D` calls `confirm(X)` → size 3 ≥ 3 → `execute_request` runs and transfers funds.
6. Only `C` and `D` are current, trusted confirmers (2 of 3 remaining live members), yet the request executed as if 3 live confirmations were obtained — `B`'s stale, revoked vote was used to reach quorum.

### Citations

**File:** multisig2/src/lib.rs (L239-242)
```rust
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
                }
```

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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
