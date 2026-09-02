Confirmed: the same pattern exists in both `multisig/src/lib.rs` (`DeleteKey` action, [1](#0-0) ) and `multisig2/src/lib.rs` (`delete_member`, [2](#0-1) ). Both only purge requests *originated* by the removed key/member, not confirmations *given* by that key/member on requests originated by others.

### Title
Stale confirmations from removed multisig members/keys still count toward execution threshold - (`multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
When a multisig key or member is removed, only the requests they *created* are purged. Any confirmation they previously cast on requests created by other members is left untouched in the `confirmations` map, and continues to count toward `num_confirmations` even though the confirming party is no longer part of the multisig.

### Finding Description
The intended invariant of the multisig is: a request executes only once it has `num_confirmations` approvals from members who are currently part of the multisig — i.e. `confirmations recorded == confirmations from live members`.

In `multisig2/src/lib.rs`, `confirm()` counts set membership purely by length: [3](#0-2) . The only place confirmations are cleaned up in response to membership changes is `delete_member()`, which filters requests by `r.member == member` — the *originating* member of a request, not confirmers of it: [4](#0-3) 
This means if member `C` confirms a request `R` that was *created by a different member* (e.g. `A`), and `C` is later removed via `DeleteMember`, `R`'s `confirmations` set still contains `C`. `C`'s stale confirmation is never purged because `r.member` refers to `R`'s creator (`A`), not to `C`.

The exact same bug class exists in the original `multisig/src/lib.rs`'s `DeleteKey` action, which filters by `r.signer_pk == pk` (the request's original signer) rather than scanning `confirmations` for the deleted key: [1](#0-0) .

This is the direct analog of the reported `_decrementWeightUntilFree` bug: both stem from applying a filter/removal keyed on the wrong dimension (deprecated-gauge / request-originator) instead of correctly re-validating every counted unit (weight per gauge / confirmation per signer) against the current authoritative state (non-deprecated gauges / live members).

### Impact Explanation
This breaks the "confirmations counted vs. live members" custody binding explicitly called out in scope. A request configured to require `num_confirmations` (e.g. 3-of-4) can execute with fewer than that number of *genuinely authorized, currently live* members, because a stale confirmation from a removed member/key is silently retained and counted. This directly maps to the listed Critical impact: "a multisig request executed below threshold." Since multisig actions include `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, `DeployContract`, etc., a request executed with an effectively-below-threshold set of live approvers can move NEAR, deploy malicious code, or add unauthorized keys — all custody-critical actions — without the intended number of current-member approvals.

### Likelihood Explanation
Triggering this requires: (1) a request created and partially confirmed (below threshold) by some members, including one member `X`; (2) `X` subsequently being removed from the multisig via a separately-approved `DeleteMember`/`DeleteKey` request that does not touch `X`'s confirmation on the earlier pending request; (3) enough additional confirmations from currently-live members to reach `num_confirmations` counting `X`'s stale entry. This does not require any single external attacker, but it also does not require anyone to act maliciously outside the documented, normal multisig workflow (member turnover with in-flight pending requests is a foreseeable operational scenario, not a contrived edge case) — the contract itself fails to preserve its stated confirmation-threshold guarantee across membership changes. This is a code-level correctness bug rather than a hypothetical/best-practice note.

### Recommendation
When removing a member/key, iterate over **all** pending requests' `confirmations` sets (not just requests originated by that member/key) and remove the departing member's/key's entry from each. Alternatively, when counting confirmations in `confirm()`, filter the confirmation set to only those signers who are still present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request(R)` (transfer funds) — `R.member = A`, `confirmations(R) = {}`.
3. `C` calls `confirm(R)` → `confirmations(R) = {C}` (1/3, not enough).
4. Separately, `A, B, D` fully confirm and execute a `DeleteMember { member: C }` request `R2` (created by someone other than `C`, e.g. `B`). In `delete_member`, the filter `r.member == member` only matches requests originated by `C`; since `R` was originated by `A`, `R`'s confirmation set is untouched — `confirmations(R)` remains `{C}` even though `C` is now removed from `self.members`.
5. Members are now `[A, B, D]`.
6. `B` calls `confirm(R)` → `confirmations(R) = {C, B}` (2/3, not enough).
7. `D` calls `confirm(R)` → `confirmations(R).len() == 3 >= num_confirmations(3)` → `execute_request(R)` fires, transferring funds.
8. `R` executed with only 2 live-member approvals (`B`, `D`) plus one stale approval from removed member `C`, i.e. below the true 3-live-member threshold intended by `num_confirmations = 3`.

### Citations

**File:** multisig/src/lib.rs (L198-215)
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
