### Title
Stale confirmations from removed multisig members are still counted toward `num_confirmations`, allowing a request to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending *requests that were created by* the removed member; it never scans and removes that member's *confirmations on requests created by other members*. Because `confirm()` counts confirmations purely from the `confirmations: LookupMap<RequestId, HashSet<String>>` set without re-checking that each entry still corresponds to a current member of `self.members`, a request can be executed with fewer live-member confirmations than `num_confirmations` requires.

### Finding Description
The intended custody/authorization binding for the multisig is:

`confirmations counted for a request == confirmations from members who are currently in self.members`

In `confirm()`:
```rust
// multisig2/src/lib.rs:292-315
pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
    self.assert_valid_request(request_id);
    let member = self.current_member().unwrap_or_else(...);
    let mut confirmations = self.confirmations.get(&request_id).unwrap();
    ...
    if confirmations.len() as u32 + 1 >= self.num_confirmations {
        let request = self.remove_request(request_id);
        self.execute_request(request)
    } else {
        confirmations.insert(member.to_string());
        ...
    }
}
```
`confirm()` validates that the *caller* confirming right now is a current member (via `current_member()` / `assert_valid_request()`), but it takes `confirmations.len()` (the historical set already stored) at face value and never re-validates that every entry in that set still belongs to `self.members`.

The only place confirmations get cleaned up for member removal is `delete_member`:
```rust
// multisig2/src/lib.rs:355-379
fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
    ...
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
```
This loop filters on `r.member == member`, i.e. it only removes requests where the removed member was the **creator/signer** of the request (`MultiSigRequestWithSigner.member`). It does nothing about *other, still-pending* requests on which the removed member previously called `confirm()` and is recorded in `self.confirmations`. Those stale entries remain in the `HashSet<String>` for that `request_id` and keep counting toward `confirmations.len() as u32 + 1 >= self.num_confirmations` in a future `confirm()` call.

### Impact Explanation
This breaks the equality that the multisig's whole security model depends on: *"a multisig request executed below threshold"* — explicitly a Critical-impact class. Concretely: with `num_confirmations = K` and members `A..N`, a request created by member `A` can accumulate a confirmation from member `C`. If `C` is later removed via `DeleteMember` (a legitimate governance action, e.g. because `C`'s key was compromised or `C` left the organization), `C`'s stale confirmation on `A`'s pending request is never purged. Any two additional *live* confirmations (e.g. from `A` and `B`, who could be colluding or just careless) can now push the count to `K`, even though only 2 live members actually approved the request. The request (which can be an arbitrary `Transfer`, `AddKey`, `AddMember`, `FunctionCall`, etc.) then executes via `execute_request`, moving funds or granting access with fewer genuine approvals than the multisig's documented `K`-of-`N` guarantee.

### Likelihood Explanation
No privileged role is required beyond being (or having been) an ordinary multisig member — the exact "unprivileged attacker" bar in scope. The precondition (some other, unrelated member being removed while they have an outstanding confirmation on a pending request created by someone else) is a normal, expected sequence of multisig operations (member rotation while requests are in flight), not a contrived edge case. The `ACTIVE_REQUESTS_LIMIT`/`REQUEST_COOLDOWN` mechanics do not prevent this, since a request can remain pending for the cooldown window while membership changes.

### Recommendation
When removing a member in `delete_member`, also scan `self.confirmations` for *all* pending requests (not just ones created by that member) and strip the removed member's entry from each `HashSet<String>`. Alternatively, have `confirm()` recompute the confirmation count by filtering `confirmations` against `self.members.contains(...)` before comparing to `num_confirmations`, so stale/removed-member confirmations never count.

### Proof of Concept
1. Deploy `MultiSigContract::new(members = [A, B, C, D, E], num_confirmations = 3)`.
2. Member `A` calls `add_request(X)` where `X` is `Transfer { amount, receiver_id: attacker_account }`. (`multisig2/src/lib.rs:170-200`, request stored with `member = A`, empty confirmation set.)
3. Member `C` calls `confirm(request_id)` — legitimately approves, `confirmations = {C}` (len 1).
4. Members later execute a separate, legitimate `DeleteMember { member: C }` request (requires 3 confirmations, unrelated to step 2/3) to remove `C` from the multisig — e.g., because `C`'s key was rotated. `delete_member` (`multisig2/src/lib.rs:355-379`) only removes requests created by `C`; request `X` (created by `A`) is untouched, and `confirmations[request_id_X]` still contains `C`.
5. Now only `A, B, D, E` are live members. Member `B` calls `confirm(request_id_X)`: `confirmations.len() == 1` (`{C}`) `+ 1 == 2 < 3` → not yet executed, `confirmations = {C, B}`.
6. Member `A` calls `confirm(request_id_X)`: `confirmations.len() == 2` (`{C, B}`) `+ 1 == 3 >= num_confirmations` → `execute_request` runs the `Transfer` to the attacker account.
7. Result: the transfer executed with confirmations effectively from only 2 live members (`A`, `B`) plus one stale, no-longer-valid confirmation from removed member `C`, violating the 3-of-5 live-member guarantee and moving funds that should have required one more genuine live approval. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** multisig2/src/lib.rs (L108-133)
```rust
#[derive(BorshStorageKey, BorshSerialize)]
pub enum StorageKeys {
    Members,
    Requests,
    Confirmations,
    NumRequestsPk,
}

#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
pub struct MultiSigContract {
    /// Members of the multisig.
    members: UnorderedSet<MultisigMember>,
    /// Number of confirmations required.
    num_confirmations: u32,
    /// Latest request nonce.
    request_nonce: RequestId,
    /// All active requests.
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
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
