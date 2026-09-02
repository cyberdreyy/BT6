## Title
Stale confirmations from removed multisig members count toward the K-of-N threshold, allowing a request to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests and confirmations that were *created* by the removed member; it never scans the `confirmations` map for votes that removed member cast on *other* members' requests. Those stale confirmations remain counted by `confirm()`, so a request can later be executed with fewer live-member votes than `num_confirmations` requires. The same defect exists in the legacy `multisig/src/lib.rs` via `DeleteKey`.

### Finding Description
`add_request` records a `MultiSigRequestWithSigner { member, ... }` where `member` is the *creator* of the request [1](#0-0) . Any other member can call `confirm(request_id)`, which inserts their identity into the `confirmations: LookupMap<RequestId, HashSet<String>>` set for that request and executes once the set size plus the caller reaches `num_confirmations` [2](#0-1) .

When a member is removed via the `DeleteMember` action, `delete_member` is invoked:

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
        self.confirmations.remove(&request_id);
        self.requests.remove(&request_id);
    }
    self.num_requests_pk.remove(&member.to_string());
    self.members.remove(&member);
    ...
}
``` [3](#0-2) 

This filter matches only requests where `r.member == member`, i.e. requests the departing member *created*. It does not walk `self.confirmations` to strip that member's votes from requests created by *other* members. Consequently, if the departing member had previously called `confirm()` on someone else's still-pending request, their entry stays in that request's `confirmations` `HashSet` forever, even though `self.members` no longer contains them.

`confirm()`'s threshold check only counts set size, never checking that each entry in the set is still a current member:
```rust
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [4](#0-3) 

This breaks the intended custody binding: **confirmations counted == confirmations from live members**. After the removal, the binding becomes `confirmations counted > live-member confirmations`, letting the K-of-N threshold be satisfied by fewer than K currently-authorized members.

The identical pattern exists in the legacy `multisig` contract, where `DeleteKey` only clears requests whose `signer_pk` equals the deleted key, not confirmations that key cast on other requests [5](#0-4) .

### Impact Explanation
This directly matches the "Critical" impact category: *a multisig request executed below threshold*. A NEAR `Transfer`, `AddKey`, `FunctionCall`, or `DeployContract` request can be executed with confirmations from fewer live members than `num_confirmations` mandates, because a stale confirmation from an already-removed member is still tallied. This allows funds to move, keys to be added, or code to be deployed without the number of currently-trusted signers the multisig was configured to require — an authorization boundary crossed via the multisig's own bookkeeping bug, not by a bypass of external checks.

### Likelihood Explanation
The precondition is realistic and requires no privileged foundation/owner role beyond being one of the multisig's own members (an inherent part of using this contract): a member confirms a request created by someone else, then is later removed via a normal `DeleteMember` action (routine key rotation/offboarding). Any surviving member can then complete the now under-confirmed request. No malicious deploy, no owner abuse, no key theft is required — only ordinary multisig operation combined with member turnover, which is an expected lifecycle event for these contracts.

### Recommendation
When removing a member (`delete_member` / `DeleteKey`), iterate over all pending requests' `confirmations` sets (not just those created by the removed member) and remove the departing member's/key's entry from each. Alternatively, validate at `confirm()`-time (before counting toward the threshold) that every entry in the `confirmations` set still corresponds to a current member, discarding stale entries lazily.

### Proof of Concept
Setup: multisig2 initialized with 3 members `A, B, C` and `num_confirmations = 2`.

1. `A` calls `add_request(R)` where `R` is `Transfer { amount }` to some receiver. `R.member = A`. `confirmations[R] = {}`.
2. `B` calls `confirm(R)`. Since `0 + 1 = 1 < 2`, this just records `confirmations[R] = {B}` (no execution yet).
3. `A` and `C` submit and confirm a separate `DeleteMember { member: B }` request (2-of-3), which executes `delete_member(B)`:
   - `self.members.len() - 1 = 2 >= num_confirmations (2)` → assertion passes.
   - The filter `r.member == B` finds no requests (since `B` never created one), so `confirmations[R] = {B}` is **not** cleared.
   - `B` is removed from `self.members`.
4. Now the live member set is `{A, C}`, yet `confirmations[R]` still equals `{B}`.
5. `A` calls `confirm(R)`. `assert_valid_request` passes (`A` is a current member, `R` exists). `confirmations.len() as u32 + 1 = 1 + 1 = 2 >= num_confirmations (2)` → true. `execute_request(R)` runs, transferring funds.

Result: the `Transfer` request executed with only one live member (`A`) actually authorizing it in real time — `B`'s stale, pre-removal confirmation was counted as if it were a current signer — even though the contract requires 2 confirmations from the current 2-member set. This is a K-of-N threshold bypass enabling unauthorized fund movement.

### Citations

**File:** multisig2/src/lib.rs (L188-200)
```rust
        // add the request
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
    }
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
