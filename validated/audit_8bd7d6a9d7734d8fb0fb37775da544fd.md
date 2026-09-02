### Title
Multisig `confirm()` Counts Stale Confirmations From Removed Members, Allowing Request Execution Below the Live-Member Threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
The bug class in the report is a bounded/limited traversal whose accounting (count of "found" higher-level parents) is never revalidated against the actual current state, letting an attacker manipulate the underlying set so the count diverges from reality and an unintended fallback path executes. The same root cause exists in `multisig2/src/lib.rs` and `multisig/src/lib.rs`: `confirm()` compares the *size* of the stored `confirmations` set to `num_confirmations`, but member/key removal (`delete_member` / `DeleteKey`) never purges a removed member's/key's existing confirmations from other pending requests' confirmation sets. This lets a request execute with fewer genuine confirmations from currently-live members than `num_confirmations` requires.

### Finding Description
`MultiSigContract::confirm()` in `multisig2/src/lib.rs` decides to execute a request purely by set cardinality: [1](#0-0) 

`current_member()` only validates that the *caller of the current transaction* is presently a member; it never re-checks whether the *previously recorded* entries in `confirmations` still belong to current members.

The only place membership removal is handled is `delete_member()`: [2](#0-1) 

This function removes the member from `self.members` and deletes only the requests **originated** by that member (`r.member == member`). It does **not** scan `self.confirmations` for other pending requests where the removed member had already cast a confirmation, so that stale confirmation string remains in the `HashSet<String>` for those other requests.

Consequently, the invariant the design intends — "a request executes only once `num_confirmations` *live* members have approved it" — is broken. The actual binding enforced is:

```
confirmations.len() >= num_confirmations
```

instead of the intended:

```
|{live members who confirmed}| >= num_confirmations
```

The identical pattern exists in the older `multisig/src/lib.rs`, where `DeleteKey` only removes requests originated by the deleted key, not that key's confirmations recorded on other pending requests: [3](#0-2) 

### Impact Explanation
This is a direct authorization-threshold bypass: a multisig request can be executed (including `Transfer`, `FunctionCall`, `AddKey`/`AddMember` actions that move NEAR or grant privileged access) with fewer than `num_confirmations` approvals from members who are actually live/trusted at execution time. This matches the explicitly listed Critical impact category "a multisig request executed below threshold" — funds can move, or privileges can be granted, without the intended quorum of currently-trusted parties having approved.

### Likelihood Explanation
The precondition is realistic and does not require any privileged attacker action beyond normal contract usage: a member (e.g., one whose key is later suspected compromised or who is being offboarded) confirms a pending request, then the remaining members vote to remove that member via `DeleteMember`/`DeleteKey`. Because `delete_member`/`DeleteKey` only purge requests *originated* by the removed member, the removed member's stale confirmation persists and continues to count toward the threshold on any other request they had confirmed before removal, letting the remaining fewer live members reach `num_confirmations` sooner than intended. No special privileges beyond being (or having been) a legitimate multisig member/keyholder are required to create the scenario; execution of the affected request can be triggered by any current member.

### Recommendation
When removing a member (`delete_member`) or key (`DeleteKey`), also scan `self.confirmations` for all pending requests and remove the entry corresponding to the removed member/key, not just the requests they originated. Alternatively, re-validate at `confirm()` time that every entry in the stored `confirmations` set still corresponds to a current member before comparing cardinality to `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. Member `A` calls `add_request_and_confirm(request_X)` — `confirmations[X] = {A}`.
3. The remaining members `B`, `C`, `D` submit and confirm a separate request that includes `DeleteMember { member: A }` (satisfying `self.members.len() - 1 >= num_confirmations`, e.g. 4 members - 1 = 3). `A` is removed from `self.members`; `delete_member` only deletes requests where `r.member == A`, so `request_X` (created by `A` but originated by `A`... to avoid that, have `B` originate `request_X` and `A` merely confirm it) is untouched — `confirmations[X]` still contains `A`.
4. Now `B` calls `confirm(X)` → `confirmations[X] = {A, B}` (len 2, `< 3`, stored).
5. `C` calls `confirm(X)` → `confirmations[X].len() + 1 = 3 >= num_confirmations (3)` → `execute_request` fires.
6. Result: `request_X` executes with confirmations effectively from `A` (removed/no-longer-trusted member) + `B` + `C`, i.e., only 2 currently-live members actually approved, one hop below the intended 3-of-N live-member threshold — matching the "confirmations counted versus live members" divergence and the "multisig request executed below threshold" impact.

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
