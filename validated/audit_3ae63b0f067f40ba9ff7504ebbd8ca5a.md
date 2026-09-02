## Title
Stale confirmations from removed multisig members still count toward the execution threshold, allowing requests to execute below the required live-member quorum - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges pending requests that were *originally submitted* by the removed member; it never scans the `confirmations` sets of requests submitted by *other* members to strip out that member's prior confirmation. Since `confirm()` bases the execution decision purely on `confirmations.len()`, a confirmation cast by a member who has since been removed from the multisig continues to count toward `num_confirmations`, allowing a request to execute with fewer than `num_confirmations` *live* members having approved it.

### Finding Description
`confirm()` computes whether to execute a request purely from the stored confirmation set size, without checking that each confirming identity is still a current member: [1](#0-0) 

`delete_member`, invoked from `execute_request` on a `DeleteMember` action, removes only the *requests the removed member had authored* (`r.member == member`), together with those specific requests' confirmation sets. It does not touch confirmation sets belonging to other, still-pending requests that the removed member may have previously confirmed: [2](#0-1) 

Because of this, a confirmation recorded by a member before their removal remains embedded in the `confirmations` `HashSet<String>` of any request created by someone else, and is never invalidated. `assert_valid_request`/`current_member()` only validate the *caller* of the current transaction is a live member — they do nothing to validate the historical entries already stored in `confirmations`: [3](#0-2) 

The equivalent flaw exists in the older `multisig/src/lib.rs` contract, where `DeleteKey` only removes requests whose `signer_pk` equals the deleted key, leaving that key's confirmations on other pending requests intact: [4](#0-3) [5](#0-4) 

The invariant the contract is supposed to enforce is: `count({m ∈ live_members : m confirmed request}) >= num_confirmations`. What is actually enforced is: `count({m : m confirmed request at any point in history}) >= num_confirmations`, which is a strictly weaker and violable condition once membership changes.

### Impact Explanation
This breaks the core custody guarantee of a K-of-N multisig: that any `Transfer`, `DeployContract`, `AddKey`, or `FunctionCall` action requires `num_confirmations` *currently authorized* signers. An action can be executed with only `num_confirmations - 1` (or fewer) live members actively approving it, with the remainder of the quorum supplied by a stale vote from someone no longer entrusted with signing authority (e.g., an employee who left, a compromised key that was intentionally revoked, or a member removed specifically because they were no longer trusted). This is a multisig request executed below the intended threshold, directly enabling unauthorized transfer of NEAR, unauthorized deployment of new contract code, or unauthorized addition of a full-access key to the account.

### Likelihood Explanation
This requires no privileged access beyond being (or having recently been) a legitimate multisig member — a completely ordinary, expected multisig workflow: member A creates a request, member B (who is later removed for any routine reason) confirms it, then the request sits pending until the remaining current members reach what they believe is quorum. No malicious deployment, redeploy, or foundation/owner privilege is needed; it is a natural consequence of member turnover combined with long-lived pending requests (`REQUEST_COOLDOWN` explicitly anticipates requests staying open for extended periods).

### Recommendation
When removing a member (`delete_member` / `DeleteKey`), iterate over all pending requests' confirmation sets and strip the removed member's/key's entry from every one of them (not just requests they authored), or alternatively validate at `confirm()`-time / execution-time that every entry in a request's confirmation set still corresponds to a current member before counting it toward the threshold.

### Proof of Concept
1. Initialize a `MultiSigContract` with 5 members `{A, B, C, D, E}` and `num_confirmations = 3`.
2. `A` calls `add_request` with a `Transfer` (or `AddKey`) action targeting an attacker-controlled receiver/key.
3. `B` calls `confirm(request_id)` → confirmations = `{B}` (1/3, not executed).
4. `C` calls `confirm(request_id)` → confirmations = `{B, C}` (2/3, not executed).
5. Separately, members submit and confirm a `DeleteMember { member: B }` request that reaches quorum and executes via `execute_request` → `delete_member`. Per [6](#0-5)  this only deletes requests where `r.member == B` (none, since the pending Transfer request was authored by `A`), so the Transfer request's confirmation set `{B, C}` is left untouched even though `B` is now removed from `self.members`.
6. `D` (a legitimate current member) calls `confirm(request_id)`. In `confirm()`, `confirmations.len() as u32 + 1` = `2 + 1 = 3 >= num_confirmations (3)`, so the request executes via `self.execute_request(request)` — even though only `C` and `D` are actually current members who approved it; `B`'s stale vote filled the gap.
7. Result: the Transfer/AddKey action executes with only 2 of 4 current members' approval against a nominal 3-of-N policy, moving funds or granting access-key control without meeting the intended live-member threshold.

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
