Confirmed: the same stale-confirmation pattern exists in both `multisig/src/lib.rs` (`DeleteKey`) and `multisig2/src/lib.rs` (`DeleteMember`). Only confirmations/requests *authored* by the removed key/member are purged; confirmations that removed key/member cast on requests *authored by other, still-active members* are never cleaned up, yet they keep counting toward `num_confirmations` in `confirm()`.

### Title
Stale confirmations from deleted multisig members/keys are still counted toward the confirmation threshold, allowing requests to execute below the live-member threshold - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
`confirm()` decides whether to execute a request purely by comparing the size of the stored `confirmations` set (plus the current confirmer) to `num_confirmations` [1](#0-0) [2](#0-1) . `delete_member` / the `DeleteKey` action only remove requests and confirmations that the removed member/key *authored* — they never scrub that member's/key's confirmation entries from other, unrelated pending requests [3](#0-2) [4](#0-3) . As a result, a confirmation cast by a member/key that is later removed remains permanently counted, letting a request reach `num_confirmations` with fewer *live* approving members than the policy requires.

### Finding Description
The binding that should hold is: `confirmations counted toward execution == confirmations from currently-live members`. This invariant is broken because:

1. Member/key `D` confirms request `R` (authored by a different member `A`), so `confirmations[R] = {D}` [1](#0-0) .
2. The multisig later removes `D` via a `DeleteMember` (`multisig2`) or `DeleteKey` (`multisig`) request. `delete_member`/`DeleteKey` handling filters `self.requests` for entries whose *author* (`r.member`/`r.signer_pk`) equals the removed member/key, and only clears confirmations for those authored requests [5](#0-4) [6](#0-5) . Since `R` was authored by `A`, not `D`, its confirmation set `{D}` is untouched — `D` is now deleted from `self.members` (`multisig2`) or has its access key removed (`multisig`), yet `D`'s stale confirmation on `R` still exists in storage.
3. Any subsequent live member confirming `R` counts `D`'s stale confirmation toward the threshold check `confirmations.len() as u32 + 1 >= self.num_confirmations` [7](#0-6) , so `R` (which can be a `Transfer`, `AddKey` full-access-key grant, `FunctionCall`, or `DeployContract`) executes with one fewer *live* approval than `num_confirmations` mandates.

This is directly analogous to the reSDL bug: a piece of state (`queuedRESDLSupplyChange` there, a stored `confirmations` set here) that should be recomputed/invalidated in light of a parameter change (`maxBoost` decrease there, member/key removal here) is instead carried forward stale, and that stale value is later trusted to authorize an action (reward routing there, fund transfer/privilege escalation here) — a violation of the "confirmations counted vs. live members" custody binding called out explicitly in the rules, with impact matching the listed Critical bucket ("a multisig request executed below threshold").

### Impact Explanation
This directly matches the explicitly-listed Critical impact: "a multisig request executed below threshold." A request able to move NEAR (`Transfer`), grant a full access key (`AddKey`), deploy new contract code (`DeployContract`), or invoke an arbitrary `FunctionCall` on behalf of the multisig account can be executed with the approval of fewer *live* members than the configured `num_confirmations`, because a departed member's/key's leftover confirmation is silently counted. This is an unauthorized-move-of-funds / authorization-boundary-crossing scenario, not merely a griefing or DoS issue.

### Likelihood Explanation
The pattern requires only ordinary, expected multisig operation: (a) a member/key confirms a request that it did not author, and (b) that member/key is later removed for any routine reason (rotation, suspicion of compromise, offboarding) while that unrelated request is still pending. Both `add_request`/`confirm` (confirming someone else's request) and `DeleteMember`/`DeleteKey` (removing a member/key) are normal, frequently-used multisig operations, so the precondition is easy to reach without any privileged bypass — it does not require ignoring documented initialization nor any owner/foundation misconduct, only the natural combination of two supported, independent flows.

### Recommendation
When executing `DeleteMember` (`multisig2`) or `DeleteKey` (`multisig`), scrub the removed member's/key's confirmation entry from *every* pending request's `confirmations` set (not only requests it authored), or alternatively re-validate at `confirm()`/execution time that every entry in a request's `confirmations` set still corresponds to a current member/key before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. Member `A` calls `add_request` for request `R` = `Transfer { amount: X }` to some receiver (not auto-confirmed) — `confirmations[R] = {}`.
3. Member `D` calls `confirm(R)` → `confirmations[R] = {D}` (size 1, below threshold 3, request stays pending) [8](#0-7) .
4. Members legitimately execute a separate, properly-confirmed request `DeleteMember { member: D }` to remove `D` (e.g., due to a rotated/compromised key). `delete_member` only cleans requests authored by `D`; since `A` authored `R`, `R`'s confirmations `{D}` are left intact and `D` is removed from `self.members` [9](#0-8) .
5. Now only members `{A, B, C}` remain live. Member `B` calls `confirm(R)`. The check computes `confirmations.len() as u32 + 1 >= num_confirmations` → `1 + 1 = 2 >= 3` is false, so this alone does not execute — but member `C` then confirms: `2 + 1 = 3 >= 3` → `execute_request` fires the `Transfer` [7](#0-6) .
6. Result: `R` executed with confirmations from live members `{B, C}` (2 live approvals) plus the stale, no-longer-valid confirmation from removed member `D`, satisfying a nominal threshold of 3 with only 2 *live* member approvals — one fewer than the policy requires.

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
