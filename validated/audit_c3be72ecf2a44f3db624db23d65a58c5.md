### Title
Confirmations From Removed Multisig Members Persist on Other Requests, Allowing Execution Below Intended Confirmation Threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
The multisig contracts' member/key-removal logic only deletes **requests originated by** the removed member/key; it does not scrub that member's/key's **confirmations recorded on other members' pending requests**. Because `confirm()` counts confirmations purely by set membership (`confirmations.len() >= num_confirmations`) without re-checking that each confirming entry is still a current member, a stale confirmation from an already-removed member can still be combined with confirmations from remaining members to cross the `num_confirmations` threshold, letting a request execute with fewer than `num_confirmations` *live* member approvals.

### Finding Description
`delete_member` in `multisig2/src/lib.rs` removes the member from `self.members` and deletes only the requests that member itself created: [1](#0-0) 

It never inspects `self.confirmations` for entries belonging to `member` on requests created by *other* members. Those confirmations remain stored as strings in the `LookupMap<RequestId, HashSet<String>>`: [2](#0-1) 

`confirm()` only checks whether the *current* confirmer has already confirmed and whether the confirmation-set size reaches `num_confirmations` — it never re-validates that previously recorded confirmers are still members: [3](#0-2) 

The equivalent code path exists in the legacy `multisig/src/lib.rs` contract, where `DeleteKey` removes only the requests signed by the removed key, leaving that key's confirmations on other requests untouched: [4](#0-3) [5](#0-4) 

**Binding broken:** the number of confirmations counted toward `num_confirmations` should equal the number of confirmations from *live* members: `confirmations.len() == |{live members who confirmed}|`. After a member/key removal, this equality breaks — `confirmations.len()` can include entries from accounts/keys no longer in `self.members`, so `confirmations.len() >= num_confirmations` can be true while `|{live members who confirmed}| < num_confirmations`.

### Impact Explanation
This lets a request execute (transferring NEAR, deploying/upgrading a contract, adding a full-access key, or calling an arbitrary function on the contract's behalf) with genuine approval from fewer live members than the configured `num_confirmations`. This is precisely the "multisig request executed below threshold" scenario, which the rules classify as **Critical**: it is an authorization boundary crossed by an unprivileged/removed party's stale approval, enabling actions (including irreversible ones like `Transfer` or `AddKey`) that the current member set never actually authorized to the required degree.

This is especially severe in the realistic case where a member/key is removed **because it was compromised or the key holder is being revoked** — the entire point of removal is to strip that key's influence, yet its already-cast confirmation continues to count.

### Likelihood Explanation
Reachable by any current members operating normally (no special privilege beyond being a member, which is the expected caller of these methods) as long as:
1. A confirmation is recorded on a request before the confirming member is removed, and
2. That request is not itself confirmed to completion or deleted before the removal.
This is a routine sequence (member churn while requests are outstanding) rather than a contrived edge case, and `active_requests_limit` (default 12) means multiple pending requests per signer are expected/allowed, increasing the chance of overlap between "member being removed" and "requests they've already confirmed."

### Recommendation
When removing a member/key (`delete_member` in `multisig2`, `DeleteKey` handling in `multisig`), also scan `self.confirmations` for **every** request (not just requests the removed member created) and strip the removed member's/key's entry from each confirmation set. Alternatively, change `confirm()`/execution-threshold checks to only count confirmations from entries that are still present in `self.members` (i.e., intersect the stored confirmation set with current members before comparing to `num_confirmations`).

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `D` calls `add_request` with a harmful `MultiSigRequestAction::Transfer` (or `AddKey`) request `R1`; `R1` is now stored with `member = D`.
3. `A` calls `confirm(R1)` → `confirmations[R1] = {A}` (1/3).
4. Separately, `B`, `C`, `D` pass a `DeleteMember { member: A }` request through the normal 3-confirmation flow; `delete_member` removes `A` from `self.members`, and only deletes requests where `r.member == A` — `R1` (created by `D`) is untouched, so `confirmations[R1]` still contains `A`.
5. `B` calls `confirm(R1)` → `confirmations[R1] = {A, B}` (2/3).
6. `C` calls `confirm(R1)` → `confirmations[R1].len() == 3 >= num_confirmations` → `execute_request` fires and `R1` executes, even though only `B` and `C` are live members who actually approved it (`A`'s stale confirmation supplied the third vote after being removed). [6](#0-5) [7](#0-6)

### Citations

**File:** multisig2/src/lib.rs (L126-130)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
```

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
