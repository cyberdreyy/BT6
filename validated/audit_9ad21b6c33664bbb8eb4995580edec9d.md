### Title
Stale confirmations from deleted multisig members let a request execute below the live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`DeleteMember`/`DeleteKey` only purges pending *requests created by* the removed member, not *confirmations cast by* that member on other members' requests. Because `confirm()` counts entries in the `confirmations` `HashSet` without checking that each confirming identity is still a current member, a confirmation recorded before removal keeps counting toward `num_confirmations` after the member is gone, letting a request execute with fewer currently-live approvals than the configured threshold.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` decides whether to execute a request purely by comparing the size of the stored confirmation set to `self.num_confirmations`: [1](#0-0) 

When a member is removed via `DeleteMember`, `delete_member()` only cleans up requests whose *creator* (`r.member`) equals the removed member; it does not scan `self.confirmations` to strip that member's confirmation entries from requests created by someone else: [2](#0-1) 

The same asymmetry exists in the legacy `multisig` contract's `DeleteKey` handling, which likewise only removes requests signed by the deleted key, not that key's confirmations on other pending requests: [3](#0-2) 

This breaks the intended equality `live_confirmations(request) == num_confirmations_required` — the contract instead evaluates `stale_confirmations(request) + live_confirmations(request) == num_confirmations_required`, since a stale entry left by a removed member is indistinguishable from a live one in the `HashSet<String>`.

### Impact Explanation
This is a multisig request executed below the intended threshold: fewer currently-authorized members can push through a `Transfer`, `AddKey`/`AddMember`, `DeployContract`, or `FunctionCall` request than the `K` required by `num_confirmations`, because a phantom confirmation from a member who has since been removed still counts. This directly matches the Critical impact category "a multisig request executed below threshold," since funds or privileged actions on the multisig account can move with less real authorization than configured.

### Likelihood Explanation
Requires: (1) a request pending confirmation, (2) one of its confirmers subsequently removed via a separate `DeleteMember`/`DeleteKey` request (a normal, expected multisig operation, e.g. offboarding a team member or rotating a compromised key), and (3) the remaining live members continuing to confirm the original pending request afterward without deleting/recreating it. No code path currently forces re-validation or invalidation of stale confirmations on membership change, so this can occur through ordinary contract usage rather than a contrived edge case.

### Recommendation
When removing a member (`DeleteMember`/`DeleteKey`), iterate over all pending requests and strip the removed member's entry from every `confirmations` set (not just requests they created), or alternatively re-validate at `confirm()` time that every entry in the stored confirmation set still corresponds to a current member (e.g., filter `confirmations` against `self.members` before comparing length to `num_confirmations`).

### Proof of Concept
1. Multisig initialized with members `A, B, C, D` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `X` (e.g., a `Transfer`). `B` calls `confirm(X)` → `confirmations = {A? no—only B}`; assume `A` also confirms → `confirmations = {A, B}` (2/3).
3. Separately, members execute a `DeleteMember { member: B }` request (approved by 3 live members, e.g. `A, C, D`), which succeeds and removes `B` from `self.members` and deletes access key — but `X`'s `confirmations` set still contains `B`'s entry because `delete_member` only purges requests where `r.member == B` (requests `B` created), not confirmations `B` gave on `A`'s request `X`.
4. `C` (a current, live member) calls `confirm(X)`. `confirmations.len() (2, including stale B) + 1 = 3 >= num_confirmations (3)` → `execute_request` runs `X`.
5. Result: request `X` executed with only 2 truly live confirmations (`A`, `C`) plus 1 stale/phantom confirmation from removed member `B`, violating the 3-of-N threshold guarantee. [4](#0-3) [5](#0-4)

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
