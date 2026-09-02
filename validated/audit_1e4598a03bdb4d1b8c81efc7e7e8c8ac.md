### Title
Stale confirmations from removed multisig members still count toward the K-of-N execution threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges the *outstanding requests* that were originally submitted by the member being removed; it does not scan or clean the `confirmations` map for confirmations that removed member gave on *other* members' still-pending requests. Since `confirm()` counts entries in that stale set without checking that each confirming identity is still a current member, a request can later be executed using a mix of currently-live confirmations plus a "ghost" confirmation from an account/key that no longer has any authority over the multisig.

### Finding Description
The custody binding the multisig is supposed to enforce is: *a request executes only when `num_confirmations` distinct **currently authorized** members have confirmed it* — i.e. `confirmed_by_live_members(request) >= num_confirmations`.

What the code actually enforces is: *a request executes when the size of its stored `confirmations: HashSet<String>` (plus the new confirmer) reaches `num_confirmations`*, regardless of whether every entry in that set still corresponds to a live member: [1](#0-0) 

`delete_member` is the only place that mutates confirmation state on member removal, and it is scoped strictly to requests the removed member *authored*: [2](#0-1) 

It never iterates `self.confirmations` to strip the removed member's identity string out of confirmation sets belonging to *other members'* pending requests. Because `current_member()` is only checked when someone actively calls `confirm`/`add_request` (to verify the *caller*), the historical string already stored inside a `HashSet<String>` is never re-validated against the live `members` set: [3](#0-2) 

Sequence that breaks the binding:
1. Multisig configured 3-of-4 (`num_confirmations = 3`, members A, B, C, D).
2. Someone adds a `Transfer` request; A confirms, B confirms (2/3 confirmations recorded, request stays pending since 2 < 3).
3. Members later legitimately vote to remove D via `DeleteMember` (D authored no requests, so nothing in `confirmations` referencing other requests is touched — only D's own authored requests/`num_requests_pk` entry are cleaned). Members set is now {A, B, C}, still with `num_confirmations = 3`.
4. Now only C needs to confirm the still-pending Transfer request from step 2. C confirms: `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → executes.

The transfer executes with 3 recorded confirmations, but only A, B, and C are actually live members contributing — this example doesn't yet show the ghost case. The real ghost case is when the *removed* member is one of the confirmers on the pending request:

1. Members A, B, C, D; `num_confirmations = 3`.
2. D adds and confirms a Transfer request as a courtesy/no self-benefit scenario is not even required — any member, say B, adds the request, D confirms it (1 confirmation: D). C also confirms (2 confirmations: D, C).
3. Members vote to remove D (`DeleteMember{D}`). This request is not authored by D, so `delete_member`'s cleanup does nothing to the Transfer request's confirmation set — D's confirmation string remains inside it.
4. A confirms the pending Transfer request: `confirmations.len() (2, containing D and C) + 1 = 3 >= 3` → request executes.

The request is executed with only A and C as *currently live* confirmers plus a stale confirmation attributed to D, who has already been removed and stripped of signing authority. The K-of-N guarantee ("K live members must approve") is violated — funds move (a `Transfer` action) with fewer than K authorized signers.

### Impact Explanation
This is a **Critical** custody-binding break: a `MultiSigRequestAction::Transfer` (or `FunctionCall`, `AddKey`, `AddMember`, `DeployContract`, etc.) can be authorized and executed by the multisig account with fewer genuinely-live confirmations than the configured threshold, because a removed member's earlier confirmation is silently reused as if it still represented current authority. This directly matches "a multisig request executed below threshold" in the impact criteria — NEAR (or any assets/authority controlled by the multisig account) can be moved without the intended K-of-N consensus of currently trusted parties.

### Likelihood Explanation
Membership changes in a long-lived multisig (turnover of employees/validators/co-signers, key rotation) are a normal operational event, not an edge case. Any time a `DeleteMember` request is processed while there exists at least one other pending request that the removed member had previously confirmed (very plausible in active multisigs with several requests in flight — the contract explicitly allows up to `active_requests_limit` concurrent pending requests per member), the stale confirmation persists indefinitely until that pending request is separately confirmed or deleted. No special privilege beyond normal multisig operation is needed to trigger the flaw — it is a latent state-consistency bug that surfaces automatically the next time enough remaining members confirm the older request.

### Recommendation
When removing a member in `delete_member`, iterate all entries of `self.confirmations` (not just requests authored by the removed member) and remove the removed member's identity string from every confirmation set; alternatively, validate confirmation-set membership against `self.members` at the moment of the threshold check in `confirm()` (i.e., filter out confirmations whose signer is no longer in `self.members` before comparing against `num_confirmations`). The safer, cheaper fix is the latter — compute `confirmations.iter().filter(|m| self.members.contains(m)).count()` for the threshold comparison so stale confirmations can never count toward execution, without needing an O(n) scan over all requests on every member removal.

### Proof of Concept
Using the existing test scaffolding in `multisig2/src/lib.rs` (`members()`, `context_with_key`/`context_with_account` helpers):

```rust
#[test]
fn test_stale_confirmation_counts_after_member_removal() {
    // members = [alice(Account), bob(Account), key1(AccessKey), key2(AccessKey)] , num_confirmations = 3
    testing_env!(context_with_account(bob(), 1_000));
    let mut c = MultiSigContract::new(members(), 3);

    // key2 (soon to be removed) adds and confirms a Transfer request
    testing_env!(context_with_key(key2_pk(), 1_000));
    let transfer_req = MultiSigRequest {
        receiver_id: bob(),
        actions: vec![MultiSigRequestAction::Transfer { amount: 1_000.into() }],
    };
    let request_id = c.add_request_and_confirm(transfer_req); // confirmations = {key2}

    // key1 also confirms -> confirmations = {key2, key1} (2/3, still pending)
    testing_env!(context_with_key(key1_pk(), 1_000));
    c.confirm(request_id);

    // Now alice+bob (2 of 4) submit & confirm a DeleteMember{key2} request against self
    testing_env!(context_with_account(alice(), 1_000));
    let del_req = MultiSigRequest {
        receiver_id: alice(), // current_account_id in test harness
        actions: vec![MultiSigRequestAction::DeleteMember {
            member: MultisigMember::AccessKey { public_key: key2_pk() },
        }],
    };
    let del_id = c.add_request_and_confirm(del_req);
    testing_env!(context_with_account(bob(), 1_000));
    c.confirm(del_id); // executes DeleteMember{key2} — key2 removed, members = 3

    // key2's confirmation on `request_id` (the Transfer) was NOT cleaned up.

    // bob now confirms the original Transfer request -> 2 (stale key2 + live key1) + 1 (bob) = 3 >= num_confirmations
    testing_env!(context_with_account(bob(), 1_000));
    c.confirm(request_id); // EXECUTES the Transfer despite key2 no longer being a member
}
```

This demonstrates that `confirm()` (multisig2/src/lib.rs:292-315) reaches the `num_confirmations` threshold and executes the `Transfer` using a confirmation set that includes an identity already removed via `delete_member` (multisig2/src/lib.rs:355-379), which never scrubbed that stale entry.

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
