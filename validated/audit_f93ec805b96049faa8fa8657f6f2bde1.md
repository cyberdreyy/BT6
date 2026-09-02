### Title
Multisig `confirm()` counts stale confirmations from removed members, allowing execution below the live-member threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
`delete_member`/`DeleteKey` only purge *requests created by* the removed member, not that member's *confirmations on other members' requests*. `confirm()` then counts the raw size of the confirmations set against `num_confirmations` without verifying every entry is still a current multisig member. A member's confirmation therefore keeps counting toward the execution threshold even after that member has been removed from the multisig, letting a request execute with fewer live confirmations than `num_confirmations` requires.

### Finding Description
In `multisig2/src/lib.rs`, `delete_member` removes outstanding requests filtered by `r.member == member` (the request's original *creator*), and clears `num_requests_pk` for that member, but it never touches the `confirmations` map for requests created by *other* members that the deleted member had already confirmed: [1](#0-0) 

`confirm()` counts confirmations purely by set length compared to `num_confirmations`, with no check that every entry in the set still belongs to a live `member`: [2](#0-1) 

`assert_valid_request` also never re-validates historical confirmations against current membership - it only checks the caller and that the request/confirmations exist: [3](#0-2) 

The same pattern exists in the original `multisig/src/lib.rs`: `DeleteKey` only purges requests whose `signer_pk == pk` (creator match), not that key's confirmations recorded on other requests, and `confirm()` likewise counts raw set size: [4](#0-3) [5](#0-4) 

This breaks the binding the multisig is supposed to enforce: `count(confirmations) == count(live member approvals)`. After a member is removed, `count(confirmations)` on any pre-existing request can still include that departed member, so the equality becomes `count(confirmations) > count(live member approvals)`.

### Impact Explanation
This is Critical: it allows a multisig request (e.g. a `Transfer` action moving NEAR out of the account, or `AddKey`/`AddMember` granting control) to be executed with fewer live, currently-authorized confirmations than `num_confirmations` mandates. Concretely: with `num_confirmations = 3` and members `{A,B,C,D}`, member B confirms a transfer request created by A (confirmations = `{B}`). B is subsequently removed from the multisig (e.g., because B was compromised or malicious) via a separate, properly-confirmed `DeleteMember`/`DeleteKey` request - the removal only purges requests *B created*, so B's confirmation on A's transfer request is untouched. Now only C and D need to confirm; when they do, `confirmations = {B,C,D}`, length 3 ≥ 3, and the request executes and transfers funds - even though only 2 of the 3 currently-live members (C and D) actually approved it at that point. This is exactly the "multisig request executed below threshold" scenario, moving NEAR out of the account with fewer authorized parties than intended.

### Likelihood Explanation
Likelihood is realistic in normal multisig lifecycle usage: membership changes (removing a compromised/departing member) are an expected, routine multisig operation, and there is no special timing or attacker sophistication required - a stale confirmation simply survives silently until unrelated future confirmations complete the threshold. The bug is triggered by ordinary combination of "confirm a request, then later remove that confirmer, then let other members finish confirming," with no code path currently preventing it.

### Recommendation
When removing a member (`delete_member` / `DeleteKey`), scrub that member's confirmation entry from every outstanding request's confirmations set, not just requests they created. Alternatively (and more robustly), when counting confirmations in `confirm()` / before executing a request, filter the confirmations set to only include entries that are still present in `self.members`, and only compare that filtered count to `num_confirmations`.

### Proof of Concept
1. Deploy multisig2 with `num_confirmations = 3`, members `{A, B, C, D}`.
2. `A.add_request(Transfer{...})` → `request_id = R` (confirmations = `{}`).
3. `B.confirm(R)` → confirmations = `{B}` (len 1 < 3, not executed). [2](#0-1) 
4. Members separately submit and confirm a `DeleteMember{member: B}` request through the normal multisig flow; it executes via `delete_member`, which only purges requests where `r.member == B` (none, since B didn't create R) - `confirmations` for `R` is left containing `B`. [1](#0-0) 
5. `B` is now removed from `self.members`.
6. `C.confirm(R)` → confirmations = `{B, C}` (len 2 < 3).
7. `D.confirm(R)` → confirmations = `{B, C, D}` (len 3 ≥ 3) → `execute_request(R)` runs, transferring funds, even though `B` is no longer a member and only `C` and `D` are live approvers. [6](#0-5)

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
