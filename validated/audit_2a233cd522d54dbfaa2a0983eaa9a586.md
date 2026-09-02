Both `multisig/src/lib.rs` and `multisig2/src/lib.rs` share the same flaw: removing a key/member only purges confirmations from requests *created by* that key/member, not confirmations *cast by* that key/member on requests created by others. This is analogous to the reported bug class in that a value used to authorize an action (confirmations counted) no longer matches the live set of entities entitled to grant it (current members), which is one of the accepted custody-binding analogs.

### Title
Stale confirmations from removed multisig members/keys count toward execution threshold - (File: multisig2/src/lib.rs, multisig/src/lib.rs)

### Summary
`delete_member` (multisig2) and the `DeleteKey` action (multisig) remove a departing member/key's own *requests* and the associated `num_requests_pk` counter, but never scan the `confirmations` map to strip that member's/key's confirmation entries from *other* members' pending requests. A request already confirmed by a member who is later removed keeps that stale confirmation counted, so it can later reach `num_confirmations` and execute with fewer live, currently-authorized confirmations than the configured threshold requires.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` checks the raw size of the confirmation set against `num_confirmations`: [1](#0-0) 

`delete_member()` only deletes requests where the request's creator (`r.member`) equals the removed member, and clears `num_requests_pk` for that member — it never walks `self.confirmations` to remove that member's string from confirmation sets belonging to requests created by *other* members: [2](#0-1) 

The equality that should hold is: `count of entries in confirmations[request_id]` == `count of those entries whose member is still in self.members`. Once a member is removed, that equality breaks — the stored set can contain phantom confirmations from ex-members, but `confirm()` doesn't re-validate membership of stored confirmers, only of the new confirmer via `assert_valid_request` → `current_member()`: [3](#0-2) [4](#0-3) 

The same pattern exists in the legacy `multisig/src/lib.rs`, where `DeleteKey` only purges requests signed by that key (`r.signer_pk == pk`), not that key's confirmations on requests created by other keys: [5](#0-4) 

### Impact Explanation
This allows a request (e.g., a `Transfer` of NEAR out of the multisig account) to execute with fewer *currently valid* confirmations than `num_confirmations` mandates, because a stale confirmation from a removed member/key is still counted. This directly matches the Critical impact category "a multisig request executed below threshold" — funds can move out of the account despite the live member set never actually reaching quorum.

### Likelihood Explanation
No privileged/owner-only action, victim key, or off-chain compromise is required beyond the multisig's own normal, documented lifecycle: (1) a request is created by one member, (2) confirmed by a second member, (3) that second member is later removed via the standard `DeleteMember`/`DeleteKey` governance action (a routine, expected operation, e.g. offboarding a compromised or departing signer), and (4) the still-pending request is then confirmed by remaining members. Any member with access to create/confirm requests around a scheduled member removal can trigger this; the only constraint is timing relative to when the removal executes, which is entirely observable on-chain.

### Recommendation
When removing a member/key, iterate all requests' confirmation sets (not just requests the member created) and remove that member's/key's entry, or alternatively re-validate at `confirm()`/execution time that every entry in the confirmation set still corresponds to a current member before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C]`, `num_confirmations = 2`.
2. `A` calls `add_request` for `Transfer { amount }` to some receiver → `request_id = R` (`A` is the creator, no auto-confirm).
3. `B` calls `confirm(R)` → `confirmations[R] = {B}` (len 1, below threshold, not executed) — see `confirm()` logic at [6](#0-5) .
4. Through normal governance, a separate request removing `B` (`DeleteMember { member: B }`) is confirmed and executed. `delete_member` only scans `self.requests` for entries created by `B`; since `R` was created by `A`, it is untouched, and `confirmations[R]` still contains `B` — see [7](#0-6) . Members are now `{A, C}`.
5. `A` (a current, live member) calls `confirm(R)`. `confirmations.len()` is `1` (stale `B` entry) `+ 1 = 2 >= num_confirmations (2)`, so `execute_request` runs and the transfer is executed — even though only one currently-live member (`A`) ever authorized it, one less than the configured threshold of 2.

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
