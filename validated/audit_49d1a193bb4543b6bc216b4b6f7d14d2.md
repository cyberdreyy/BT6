### Title
Stale confirmations from removed multisig members still count toward the approval threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges requests **created** by the removed member; it never scans `self.confirmations` to strip that member's votes from requests created by *other* members. `confirm()` then counts `confirmations.len()` verbatim against `num_confirmations` without checking that every entry still belongs to a live member. A request can therefore execute with fewer than `num_confirmations` approvals from members who are actually part of the multisig at execution time, breaking the "K of N live members" guarantee the contract advertises. The same pattern exists in the legacy `multisig/src/lib.rs` (`DeleteKey` handler).

### Finding Description
The intended invariant is:
```
confirmations counted at execution == approvals from members who are currently part of the multisig
```
and execution must only occur once that count reaches `num_confirmations`.

In `multisig2/src/lib.rs`:

```rust
fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
    ...
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
    ...
}
``` [1](#0-0) 

This only removes requests whose **creator** (`r.member`) is the deleted member. It does nothing about `self.confirmations` entries for requests created by *other* members that the deleted member had previously confirmed. Those stale confirmation strings remain stored.

`confirm()` then blindly trusts the stored confirmation count:
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
``` [2](#0-1) 

There is no re-validation that every string already in `confirmations` corresponds to a member still present in `self.members`. So a ghost confirmation from an already-removed member is counted the same as a live one when checking `confirmations.len() as u32 + 1 >= self.num_confirmations`.

The identical structural gap exists in the v1 contract's `DeleteKey` handling, which only purges requests created by the deleted key, not confirmations that key made on others' requests: [3](#0-2) 

### Impact Explanation
This is a "multisig request executed below threshold" scenario, explicitly a Critical-impact custody-binding break. Because a removed member's past confirmation on an unrelated request is never invalidated, a `Transfer`, `AddKey`, `FunctionCall`, or `DeployContract` request created by member C, pre-confirmed by member A, can later be pushed to execution with only `num_confirmations - 1` (or fewer) *currently live* members' actual approvals once A is removed from the multisig — the ghost entry from A silently fills one confirmation slot. This directly moves NEAR (or grants full access keys / calls arbitrary functions on behalf of the contract) with fewer signers than the multisig's configured K-of-N threshold requires, undermining the core custody guarantee of the contract.

### Likelihood Explanation
No admin/owner privilege is required beyond the ordinary ability any multisig member (or a compromised member key, which is within the standard multisig threat model) already has to call `confirm`. The only preconditions are: (1) a request exists that is confirmed by a member who is later removed via a normal `DeleteMember` request from someone other than that member, and (2) that request is not itself removed because it was not created by the member being deleted. Member turnover, key rotation, and offboarding compromised keys are ordinary, expected multisig operations, making this reachable without any special conditions or malicious deployment.

### Recommendation
When a member is deleted, iterate `self.confirmations` for every active request and remove the deleted member's entry (not just requests they created); equivalently, at execution time in `confirm()`, filter `confirmations` to only members still present in `self.members` before comparing against `num_confirmations`. Apply the same fix to the legacy `multisig/src/lib.rs` `DeleteKey` path.

### Proof of Concept
Multisig configured with members `{A, B, C, D}` and `num_confirmations = 3`.

1. `C` calls `add_request` to create request `X` (e.g. `Transfer` to an attacker-controlled account). `X.member == C`.
2. `A` calls `confirm(X)`. `confirmations[X] = {A}` (1 confirmation, below threshold of 3).
3. `B` calls `confirm(X)`. `confirmations[X] = {A, B}` (2 confirmations, still below threshold).
4. Separately, the group legitimately revokes `A` (e.g. suspected key compromise) via a fully-confirmed `DeleteMember { member: A }` request. In `delete_member`, the filter `r.member == member` only matches requests **created** by `A`; request `X` was created by `C`, so it is untouched and `confirmations[X]` still contains the stale entry `"A"`. Members are now `{B, C, D}`.
5. `D` calls `confirm(X)`. Check: `confirmations.len() as u32 + 1 >= num_confirmations` → `2 + 1 >= 3` → true. `execute_request` runs the `Transfer`.

Result: request `X` executed with the threshold "satisfied" by `{A(ghost), B, D}`, but only `B` and `D` are actual current members who approved it — `C` (the creator) never confirmed, and `A` no longer exists as a member. The transfer executed with only 2 real current-member approvals despite `num_confirmations = 3`, violating the multisig's core K-of-N guarantee. [1](#0-0) [4](#0-3)

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
