## Title
Stale confirmations from removed multisig members are still counted toward `num_confirmations`, allowing request execution below the live-member threshold - (File: multisig2/src/lib.rs, multisig/src/lib.rs)

### Summary
Both `multisig/src/lib.rs` and `multisig2/src/lib.rs` implement a k-of-n multisig where a pending request is executed once `confirmations.len() + 1 >= num_confirmations`. When a member is removed via `DeleteKey` (multisig) / `DeleteMember` (multisig2), the contract only purges **requests originated by** that member; it does not purge that member's **confirmations recorded on other still-pending requests**. Because `confirm()` never re-validates that previously stored confirmations still belong to current members, a stale confirmation from a since-removed member keeps counting toward the threshold, letting a request execute with fewer *live* member confirmations than `num_confirmations` requires.

### Finding Description
The custody/authorization binding that must hold is:
`live-member confirmations on a request >= num_confirmations` before `execute_request` runs.

In `multisig2/src/lib.rs`, `confirm()` checks: [1](#0-0) 

The threshold check is `confirmations.len() as u32 + 1 >= self.num_confirmations`, where `confirmations` is a raw `HashSet<String>` of member identifiers with no freshness or membership validation against the *current* member set — only the newly confirming caller is checked via `current_member()`.

`delete_member()` only cleans up requests that the removed member itself created, not confirmations that member placed on requests created by others: [2](#0-1) 

The equivalent code path in the original `multisig/src/lib.rs` (`DeleteKey` action) has the identical gap — it filters requests by `r.signer_pk == pk` (requests originated by the removed key) and clears those, but does not touch `self.confirmations` entries where that key is merely a confirmer on someone else's request: [3](#0-2) [4](#0-3) 

So after a member is removed, any request they had previously confirmed retains that confirmation permanently in storage, and it still counts when a live member later confirms.

### Impact Explanation
This breaks the multisig's core authorization guarantee: a `MultiSigRequestAction::Transfer`, `FunctionCall`, `AddKey`, `AddMember`, etc. can be executed with strictly fewer *live* signers than `num_confirmations` mandates, because a phantom confirmation from a removed member is silently counted. This directly matches the "Critical" impact class: a multisig request executed below threshold, potentially enabling unauthorized transfer of NEAR funds or unauthorized key/member changes with fewer live approvals than configured.

### Likelihood Explanation
This requires no more than unprivileged use of normal multisig flow: any member can `add_request`/`add_request_and_confirm` an arbitrary pending request, and separately the group can later (for entirely legitimate reasons, e.g. off-boarding) execute a `DeleteMember`/`DeleteKey` request removing that member. As long as the original request is left pending (not yet at threshold) when the member is removed, the stale confirmation persists indefinitely and can be "cashed in" later. No malicious redeploy, foundation action, or victim key compromise is required — only ordinary sequencing of legitimate multisig operations, which is realistic in long-lived multisig deployments with member turnover.

### Recommendation
When removing a member (`delete_member` in multisig2 / the `DeleteKey` branch in multisig), also strip that member's identifier from every entry in `self.confirmations` for all pending requests (not just requests it originated), or alternatively re-validate at `confirm()`-time that every entry counted in `confirmations` still corresponds to a current member (e.g., intersect the stored confirmation set with `self.members` before comparing against `num_confirmations`).

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C}` and `num_confirmations = 2`.
2. `A` calls `add_request_and_confirm(request_R)` for some sensitive action (e.g., `Transfer`) — `confirmations[R] = {A}`.
3. Separately, the group legitimately removes `A`: a `DeleteMember{A}` request is created and confirmed by `B` and `C`, reaching threshold and executing. `delete_member` removes `A` from `self.members` and deletes only requests where `r.member == A` — request `R` (created by `A` but not the *target* of removal-triggered cleanup for confirmations on other requests it's on) — note request `R` was created by `A`, so in this exact minimal case it *would* be deleted since `r.member == A`. To hit the bug precisely: instead have `B` create request `R2` and `A` merely *confirm* it (`confirmations[R2] = {A}`), then remove `A` as above — `delete_member` filters `requests` by `r.member == member` (the request's **creator**), so `R2` (created by `B`) survives untouched, and `confirmations[R2]` still contains `A`.
4. Now only `B` and `C` remain as live members. `C` calls `confirm(R2)`: `confirmations[R2].len()` is `1` (stale `A`) `+ 1 == 2 >= num_confirmations(2)`, so `execute_request` runs — with only **one** live-member confirmation (`C`'s), despite `num_confirmations = 2` and `A` no longer being a member. [5](#0-4) [6](#0-5)

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
