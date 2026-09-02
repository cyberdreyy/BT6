## Title
Stale confirmations from removed multisig members are still counted toward the execution threshold, allowing requests to execute below the required number of live confirmations - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` removes a member from `self.members`, but it only purges *requests originated by* that member — it never scans other outstanding requests' `confirmations` sets to strip that member's stale vote. `confirm()` then counts `confirmations.len()` (a raw set size) against `self.num_confirmations` without verifying that every entry in the set still corresponds to a current, live member. As a result, a confirmation cast by an account that is later removed from the multisig keeps "counting" forever, letting a request execute with fewer live signers than the configured K-of-N threshold.

### Finding Description
`delete_member` only cleans up requests that the deleted member itself created: [1](#0-0) 

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
    ...
    self.members.remove(&member);
    ...
}
```

Note that it filters `r.member == member` — i.e. it deletes a request only if the deleted member is the one who *added* it. It never iterates over `self.confirmations` of *other* outstanding requests to remove the deleted member's vote from those.

`confirm()` blindly trusts the raw size of the confirmations set as the count of authorized approvals: [2](#0-1) 

```rust
pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
    self.assert_valid_request(request_id);
    let member = self.current_member().unwrap_or_else(|| env::panic_str("Must be validated above"));
    let mut confirmations = self.confirmations.get(&request_id).unwrap();
    assert(!confirmations.contains(&member.to_string()), "Already confirmed this request with this key");
    if confirmations.len() as u32 + 1 >= self.num_confirmations {
        let request = self.remove_request(request_id);
        self.execute_request(request)
    } else {
        confirmations.insert(member.to_string());
        self.confirmations.insert(&request_id, &confirmations);
        PromiseOrValue::Value(true)
    }
}
```

`assert_valid_request` (called at the top of `confirm`) also never re-validates the pre-existing entries of `confirmations` against the current `self.members` set: [3](#0-2) 

So: a request `R` gets a confirmation from member `M`. Later, a *different* request that deletes `M` from the multisig executes successfully (via `DeleteMember`). `M`'s stale vote on `R` is never purged. When the remaining live members later confirm `R`, `confirmations.len()` still includes `M`'s vote, so the threshold `num_confirmations` is reached with fewer live signers than intended, and `execute_request` runs (e.g., a `Transfer` of NEAR, `AddKey`, `DeployContract`, etc.).

This is the direct analog of the reported Bond Protocol bug: `findMarketFor()` counted a `payout` value without checking it actually satisfied the `minAmountOut_` constraint that gave it meaning; here, `confirm()` counts `confirmations.len()` without checking each entry still satisfies the "is a live member" constraint that gives a confirmation legitimacy. The binding that should hold — `number of confirmations counted == number of live members who confirmed` — is broken once a confirming member is removed.

### Impact Explanation
This breaks the core multisig custody invariant: a `MultiSigRequestAction::Transfer` (or any other privileged action, including `AddKey`/`DeployContract`) can be executed with strictly fewer live, authorized confirmations than `num_confirmations` requires. This is a "multisig request executed below threshold" scenario, matching the Critical impact category: unauthorized/under-authorized movement of NEAR out of an account that a K-of-N scheme is supposed to protect.

### Likelihood Explanation
This does not require any privileged foundation/owner access beyond the normal multisig workflow that the contract already exposes: any set of members can (a) confirm a transfer request partially, (b) separately confirm a `DeleteMember` request removing one of the partial confirmers, and (c) finish confirming the original transfer with the remaining live members. All three actions are ordinary, unprivileged multisig operations reachable by the existing members — no redeploy, foundation key, or external actor is needed. Membership churn (adding/removing members over time) is an expected, documented multisig operation, making this reachable in normal contract lifecycle.

### Recommendation
When a member is deleted, purge that member's confirmation entry from every outstanding request's `confirmations` set (not just requests they authored), e.g. iterate `self.requests` and for each request remove `member.to_string()` from its stored `HashSet<String>`. Alternatively/additionally, in `confirm()`, recompute the effective confirmation count by filtering `confirmations` to only members currently present in `self.members` before comparing against `self.num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `A` calls `add_request` to create request `R1`: `Transfer { amount }` to an attacker-controlled or arbitrary receiver.
3. `B` calls `confirm(R1)` → `confirmations[R1] = {B}` (len 1, `1+1 < 3`, not executed).
4. Separately, `D` calls `add_request_and_confirm` for `R2`: `DeleteMember { member: B }` (self-request on the multisig account). `A` and `C` also call `confirm(R2)`, reaching 3 confirmations, so `R2` executes: `delete_member(B)` runs — per `multisig2/src/lib.rs:356-379`, this removes `B` from `self.members` and cleans up any request *authored* by `B`, but `R1` (authored by `A`) is untouched, so `confirmations[R1]` still equals `{B}`.
5. `A` calls `confirm(R1)` → `confirmations[R1] = {B, A}` (len 2, `2+1 < 3`, not executed yet).
6. `C` calls `confirm(R1)` → `confirmations.len() as u32 + 1 == 3 >= num_confirmations (3)` → `R1` executes via `execute_request`, transferring funds out, even though only `A` and `C` are live members who actually confirmed — `B`'s stale, now-invalid vote was counted toward the 3-of-4 threshold. [4](#0-3)

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
