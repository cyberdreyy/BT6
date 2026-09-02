I have enough confirmation of the vulnerability. `delete_member` only purges requests originated by the removed member (`r.member == member`) and clears `confirmations` only for those requests, but it never scans `self.confirmations` to strip the removed member's `to_string()` entries from *other* pending requests they had previously confirmed. Since `confirm()` counts `confirmations.len()` regardless of whether each entry still corresponds to a current member, stale confirmations from removed members remain valid toward the threshold. [1](#0-0) [2](#0-1) 

### Title
Stale confirmations from removed multisig members count toward the approval threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` removes a member from `self.members` and deletes only the *requests that member originated*, but it never purges that member's confirmation entries stored in `self.confirmations` for requests originated by *other* members. `confirm()` (`multisig2/src/lib.rs:292-315`) authorizes and executes a request purely by counting `confirmations.len()` against `num_confirmations`, without re-validating that every stored confirming identity is still a current member. This breaks the intended binding: confirmations counted == confirmations from live members.

### Finding Description
`delete_member` is defined at [1](#0-0) . It filters `self.requests` for entries where `r.member == member` (i.e., requests the removed member *created*) and clears confirmations only for those specific `request_id`s. It removes the member's `num_requests_pk` entry, and removes the member from `self.members`.

However, `self.confirmations` is a `LookupMap<RequestId, HashSet<String>>` keyed by request ID, containing the string identity (`member.to_string()`) of every member who has called `confirm()` on that request — including requests created by *other* members. `delete_member` has no code path that iterates all pending requests to strip the removed member's string from those `HashSet<String>` confirmation sets.

`confirm()` at [2](#0-1)  only checks that the *current caller* is a live member (via `assert_valid_request` → `current_member()`), then does `confirmations.len() as u32 + 1 >= self.num_confirmations` to decide whether to execute. It never re-verifies that the previously-stored confirmations in the set still belong to current members.

Consequently: if member A confirms a pending request created by member B, and A is later removed via `DeleteMember`, A's confirmation persists in that request's `HashSet`. When enough *additional* live members confirm, the stale count from A is added to the live count, letting the request execute with fewer genuinely current-member confirmations than `num_confirmations` requires.

### Impact Explanation
This directly matches "a multisig request executed below threshold" — a Critical-severity impact per policy, since NEAR (or contract state changes such as adding/removing keys, deploying code, transferring funds) can be moved/executed with an authorization count that no longer reflects the live member set, undermining the K-of-N security guarantee the contract is designed to provide.

### Likelihood Explanation
This requires only ordinary multisig operation flow: a member confirms a pending request, and is later removed as part of normal membership rotation (e.g., replacing a lost key, offboarding an employee, or removing a compromised member) while that request is still outstanding. No special privilege beyond normal member actions is needed; the remaining live members confirming as usual will unknowingly execute the request below the intended live-confirmation threshold. Given multisig contracts anticipate membership changes as a core supported feature, this scenario is readily reachable.

### Recommendation
When a member is deleted, iterate all pending requests and strip the removed member's identity string from every stored `confirmations` `HashSet`, not just requests the removed member originated. Alternatively, at `confirm()` time (or execution time), filter the stored confirmation set to only those entries that are still contained in `self.members` before comparing the count to `num_confirmations`.

### Proof of Concept
1. Initialize multisig with `members = {A, B, C}`, `num_confirmations = 2`.
2. B calls `add_request` creating request `X` (not yet executable).
3. A calls `confirm(X)` → `confirmations[X] = {A}` (len 1, below threshold 2).
4. Separately, B and C confirm a `DeleteMember { member: A }` request, which executes `delete_member`, removing A from `self.members`. Because `X` was created by B, not A, it is untouched by the `r.member == member` filter in `delete_member`, so `confirmations[X]` still contains `A`.
5. C calls `confirm(X)` → `confirmations[X].len() + 1 = 2 >= num_confirmations(2)` → request `X` executes, even though only one genuinely current member (C) actually confirmed it live; A's stale confirmation from before removal counted toward the threshold. [3](#0-2)

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

**File:** multisig2/src/lib.rs (L341-379)
```rust
    /// Add member to the list. Adds access key if member is key based.
    fn add_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        self.members.insert(&member.clone().into());
        match member {
            MultisigMember::AccessKey { public_key } => promise.add_access_key(
                public_key.into(),
                DEFAULT_ALLOWANCE,
                env::current_account_id(),
                MULTISIG_METHOD_NAMES.to_string(),
            ),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }

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
