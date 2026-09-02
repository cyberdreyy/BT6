Confirmed. The `delete_member` function in `multisig2/src/lib.rs` (and the analogous `DeleteKey` action in `multisig/src/lib.rs`) only removes requests originated by the removed member, but never scrubs that member's pre-existing confirmations on other pending requests they didn't author. This breaks the "confirmations counted vs live members" binding.

### Title
Stale confirmations from removed multisig members allow request execution below the live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
The multisig contract counts confirmation entries stored in a `HashSet<String>`/`HashSet<PublicKey>` keyed per request to decide when `num_confirmations` has been reached. When a member (or key) is removed via `DeleteMember`/`DeleteKey`, the contract only purges requests that member originally *added*, but does not remove that member's confirmation entries from other pending requests they had previously confirmed but did not author. The stale confirmation continues to count toward the threshold even though the member is no longer part of the multisig.

### Finding Description
In `multisig2/src/lib.rs`, `delete_member` at [1](#0-0)  filters `self.requests` for entries whose `r.member == member` (i.e., requests the removed member originated) and clears their confirmations, then removes the member from `self.members` and `self.num_requests_pk`. It never inspects the `confirmations: LookupMap<RequestId, HashSet<String>>` map for *other* requests (added by different members) where the removed member's `member.to_string()` entry is already present.

`confirm()` at [2](#0-1)  only checks `confirmations.len() as u32 + 1 >= self.num_confirmations` — it counts set membership, not whether each entry corresponds to a currently-live member from `self.members`. `current_member()` at [3](#0-2)  is only used to authorize the *caller* of the current transaction, not to revalidate historical confirmations already stored on the request.

The legacy `multisig/src/lib.rs` has the identical gap: the `DeleteKey` action at [4](#0-3)  removes only requests where `r.signer_pk == pk` (originated by that key), leaving that key's stale confirmation entries intact on other pending requests, and `confirm()` at [5](#0-4)  counts them regardless.

This is the same bug class as the CVE: a mutation path (`delete_member`/`DeleteKey`) that is supposed to fully retire an actor's standing state instead leaves stale, sensitive state (a counted "confirmation") behind, and that stale state is later trusted (counted toward `num_confirmations`) by a different, unrelated operation (`confirm`/`execute_request`) — breaking the invariant that "confirmations counted" should equal "confirmations from live members."

### Impact Explanation
This can result in a multisig request (including a `Transfer` of funds, an `AddKey`, or a `FunctionCall`) being executed with fewer approvals from currently-trusted members than `num_confirmations` requires, because one (or more) of the counted approvals belongs to a member that has since been removed. This is a Critical-tier impact per the rules: "a multisig request executed below threshold." A pending request could later be pushed over the (nominal) threshold by fewer live signers than intended, moving funds or granting keys that the remaining membership never actually collectively approved.

### Likelihood Explanation
The scenario requires: (1) a request to be added and partially confirmed by a member who did not originate it, (2) that member later being removed via a legitimate `DeleteMember`/`DeleteKey` action (a routine operation, e.g. offboarding, key rotation, revoking a compromised key), and (3) the same pending request later being confirmed to completion by the remaining live members, unaware that one counted confirmation is from a party no longer trusted. This is a realistic operational sequence for any multisig that adds/removes members over time while requests are outstanding, requiring no cooperation from an attacker beyond being the receiver of a request that happens to still be pending when a confirming member is removed.

### Recommendation
When removing a member/key, iterate all pending requests' confirmation sets (not just ones the member originated) and strip the removed member's entry from each. Alternatively, validate at `confirm`/execution time that every entry in the stored confirmation set still corresponds to a member present in `self.members`, discounting any stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Multisig deployed with `members = [A, B, C]`, `num_confirmations = 2`.
2. Member `B` calls `add_request` for a `Transfer` to attacker-controlled `receiver_id` (not yet confirmed by anyone but implicitly needs 2 confirmations).
3. Member `C` calls `confirm(request_id)` — now `confirmations = {C}` (1 of 2).
4. Members later approve a `DeleteMember { member: C }` request (e.g., because `C`'s key was compromised or `C` left the organization) via `execute_request` → `delete_member`. This only removes requests *added by* `C`; the Transfer request added by `B` still has `confirmations = {C}` untouched, and `C` is removed from `self.members`.
5. Member `A` (the only truly live member left of the previous 2-required, since `C` is gone) calls `confirm(request_id)`. `confirmations.len() as u32 + 1 = 2 >= num_confirmations(2)` is satisfied using `C`'s stale confirmation, and the Transfer executes — approved by only one currently-live member (`A`) instead of the required two. [1](#0-0) [2](#0-1)

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
