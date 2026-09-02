## Title
Stale confirmations from removed members allow a `MultiSigContract` request to execute below the true `num_confirmations` threshold — (File: `multisig2/src/lib.rs`)

### Summary
`multisig2/src/lib.rs`'s `confirm()` tallies approvals solely by `HashSet<String>` size and never re-validates that each entry in that set still belongs to `self.members`. `delete_member()` only purges *outstanding requests created by* the removed member, not confirmations that member cast on requests created by others. A member who is later removed remains permanently credited toward the threshold on any request they confirmed before removal, letting a request execute with fewer *live* member confirmations than `num_confirmations` requires.

### Finding Description
The binding that must hold is:
`live confirming members on a request >= self.num_confirmations` before `execute_request()` is called.

`confirm()` checks only the size of the raw confirmations set: [1](#0-0) 
```rust
pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
    ...
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
There is no check here that every string already stored in `confirmations` still corresponds to an account/key present in `self.members`.

`delete_member()` only cleans up requests *created* by the removed member; it does not touch the `confirmations` map for requests created by other members that the removed member had already confirmed: [2](#0-1) 
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
    ...
    self.members.remove(&member);
    ...
}
```
The filter is `r.member == member` (the request's *creator*), not a scan of every `confirmations` `HashSet` for entries matching the removed member. Consequently, a stale confirmation left by a removed member remains counted in `confirmations.len()` in the `confirm()` tally forever.

`current_member()` is only used to validate the *new* confirmer being added — it never re-validates pre-existing entries in the set: [3](#0-2) 

### Impact Explanation
This breaks the multisig's core custody guarantee — that `num_confirmations` distinct, *currently authorized* members must approve before funds move or privileged actions (`AddKey`, `AddMember`, `FunctionCall`, etc.) execute. Concretely:

1. Members `M1..M5`, `num_confirmations = 3`.
2. `M1` calls `add_request` for `Transfer{amount}` to an arbitrary receiver (no auto-confirm).
3. `M2` confirms → `confirmations = {M2}`.
4. Members later legitimately vote (via a separate `DeleteMember` request reaching 3 confirmations) to remove `M2` (e.g., a compromised or departing signer). `delete_member` only purges requests *created by* `M2`; the pending transfer request created by `M1`, which `M2` already confirmed, is untouched — `confirmations` for it still contains `M2`.
5. `M3` confirms the transfer. `confirmations.len() + 1 = 2 → still < 3` (waits).
6. `M4` confirms. `confirmations.len() + 1 = 3 >= 3` → `execute_request()` fires, transferring funds — even though only `M3`, `M4`, and one *removed, unauthorized* `M2` "confirmed" it. Only 2 of the 3 tallied approvals came from live members.

The request is executed below the true `num_confirmations` threshold, which is explicitly listed as a Critical impact ("a multisig request executed below threshold"). Funds custodied by the multisig can move (or `AddKey`/`FunctionCall` actions can execute) with fewer genuinely authorized signers than the contract's stated security model requires.

### Likelihood Explanation
No privileged access is needed beyond being (or having been) a legitimate multisig member — a normal, expected lifecycle event (member rotation/removal via `DeleteMember`, which the contract explicitly supports) is sufficient to create the stale-confirmation condition. Any request that collects a partial confirmation before one of its confirmers is removed is permanently vulnerable to this under-threshold execution; the remaining confirmers do not need to collude with the removed member, they simply need to complete confirmation later, unaware the previous confirmer no longer has authority.

### Recommendation
In `confirm()`, before comparing `confirmations.len()` against `self.num_confirmations`, filter/reconcile the confirmations set against `self.members` (drop stale entries, or count only entries currently present in `self.members`). Alternatively, in `delete_member()` and `add_member`/`DeleteMember` flow, iterate all `confirmations` maps and remove any entry matching the deleted member (not just requests they authored), so the count of confirmations always reflects live authorized members only.

### Proof of Concept
1. `MultiSigContract::new(members = [M1,M2,M3,M4,M5], num_confirmations = 3)`.
2. `M1.add_request(Transfer{amount, receiver})` → `request_id = R`, `confirmations[R] = {}`.
3. `M2.confirm(R)` → `confirmations[R] = {M2}` (len 1 < 3, no execution).
4. Members execute a separate `DeleteMember{member: M2}` request (reaching legitimate 3 confirmations) — this succeeds via `delete_member`, which only scans `requests` where `r.member == M2` (i.e., requests M2 *created*); `R` was created by `M1`, so `confirmations[R]` is left untouched, still containing `M2`. `self.members` no longer contains `M2`.
5. `M3.confirm(R)` → `confirmations[R] = {M2, M3}` (len 2 < 3).
6. `M4.confirm(R)` → `confirmations.len() as u32 + 1 = 3 >= num_confirmations(3)` → `execute_request(R)` runs, transferring funds to `receiver`, even though the tally includes a confirmation from `M2`, who is no longer a member at execution time — i.e., only 2 live-member confirmations authorized a 3-of-N transfer. [1](#0-0) [2](#0-1)

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
