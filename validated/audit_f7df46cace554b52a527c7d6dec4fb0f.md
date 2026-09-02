### Title
Stale confirmations from removed multisig members can push a request past threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges requests that were *originated* by the removed member; it never removes confirmations the removed member had cast on requests originated by *other* members. Those stale confirmations continue to count toward `num_confirmations` in `confirm`, allowing a request to execute with fewer genuinely live/authorized confirmations than the configured threshold.

### Finding Description
`confirm` counts confirmations purely by set size against `self.num_confirmations`: [1](#0-0) 

`delete_member` removes the member and deletes only the requests they created (filtered by `r.member == member`), and removes their `num_requests_pk` entry — but it does not scan `self.confirmations` to strip any confirmation the removed member had placed on requests created by *other* members: [2](#0-1) 

Because `confirmations` is a plain `HashSet<String>` keyed by the confirming member's serialized identity, and is only touched by `add_request` (new empty set), `confirm` (insert), `remove_request`/`delete_request`/`delete_member` (removal tied to request ownership, not confirmation authorship), a confirmation string left behind after its author is deleted from `members` still satisfies `confirmations.len() as u32 + 1 >= self.num_confirmations` in a later `confirm` call by a different, still-valid member.

This breaks the intended equality: `live confirming members recorded == distinct still-authorized members who confirmed`. After a member removal, the left side can include ghosts that no longer belong to `self.members` at all, so a request can execute with fewer *currently authorized* signers than `num_confirmations` requires.

### Impact Explanation
This matches the Critical bucket "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, or `AddMember` request can be pushed to execution using a confirmation from an account/key that has since been removed from the multisig, effectively lowering the real quorum required to move funds or grant access, without the removed member's continued authorization.

### Likelihood Explanation
Reaching this requires only ordinary operational sequences already supported by the contract: (1) a member confirms a pending request, (2) that member is later removed via a separate, correctly-confirmed `DeleteMember` request (a normal governance/rotation action, not a compromise), (3) a remaining member confirms the still-pending request. No malicious admin action or foundation key is needed beyond normal multisig operation — any member rotation performed while requests are outstanding can trigger it.

### Recommendation
When deleting a member in `delete_member`, iterate over all entries in `self.confirmations` (not just the requests owned by that member) and remove the member's confirmation string from every confirmation set; alternatively, iterate `self.requests`/`self.confirmations` together and re-validate that all confirming identities are still in `self.members` at `confirm` time before counting them toward the threshold.

### Proof of Concept
1. Initialize multisig with members `A, B, C, D` and `num_confirmations = 3`.
2. `A` calls `add_request(R)` (a `Transfer` to an attacker-controlled account) — not auto-confirmed.
3. `B` calls `confirm(R)` → `confirmations[R] = {B}`.
4. Members `A, C, D` (3 confirmations, satisfying the threshold on a `DeleteMember{B}` request) confirm and execute `DeleteMember { member: B }`. Since `R.member == A` (not `B`), `delete_member`'s request-purge filter does not touch `R`; `confirmations[R]` still contains `B` even though `B` is no longer in `self.members`.
5. `C` (a genuinely live, distinct member) calls `confirm(R)`: `confirmations[R].len() (1) + 1 == 2`, still short of `3`... 

To directly hit `>= num_confirmations`, use `num_confirmations = 2` instead: after step 4, only 1 more live confirmation (`C`) is needed for `R` to execute, even though `B`'s confirmation is stale — the transfer executes with only `A`'s (unconfirmed) request + `B`'s stale confirmation + `C`'s live confirmation, i.e., only one currently-authorized confirming member instead of the two required among current members. [3](#0-2) [1](#0-0)

### Citations

**File:** multisig2/src/lib.rs (L209-222)
```rust
    /// Remove given request and associated confirmations.
    pub fn delete_request(&mut self, request_id: RequestId) {
        self.assert_valid_request(request_id);
        let request_with_signer = self
            .requests
            .get(&request_id)
            .unwrap_or_else(|| env::panic_str("No such request"));
        // can't delete requests before 15min
        assert(
            env::block_timestamp() > request_with_signer.added_timestamp + REQUEST_COOLDOWN,
            "Request cannot be deleted immediately after creation.",
        );
        self.remove_request(request_id);
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
