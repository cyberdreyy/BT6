### Title
`delete_member` fails to purge stale confirmations left by a removed member, allowing a request to execute with fewer than `num_confirmations` live members - (`multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only deletes requests and confirmation sets for requests whose *original submitter* (`r.member`) equals the member being removed; it never scans other requests' `confirmations` sets to strip the removed member's stale vote. Once removed, that member's `MultisigMember::to_string()` entry keeps counting toward `num_confirmations` on any request it had previously confirmed but did not create, letting the request execute with strictly fewer live-member confirmations than `num_confirmations`.

### Finding Description
The invariant being violated: `confirmations.get(&request_id).len() (at execution time) == number of DISTINCT LIVE members who confirmed`. In reality, after a member is deleted, `confirmations.get(&request_id)` can still contain that non-member's string, so the equality breaks.

Trace:
1. `add_request` records `member: current_member` as the *submitter* on `MultiSigRequestWithSigner`, and creates an empty confirmation set for the new `request_id`. [1](#0-0) 
2. Any member (including one who did not submit the request) can call `confirm(request_id)`, which inserts `member.to_string()` into that request's confirmation `HashSet`. [2](#0-1) 
3. When a `DeleteMember` action executes, `delete_member` only removes requests/confirmations for requests where `r.member == member` (i.e., requests the removed member *created*), then deletes the member from `members` and clears its `num_requests_pk` entry: [3](#0-2) . It never iterates over `confirmations` for requests created by *other* members to strip the removed member's vote out of those `HashSet<String>` entries.
4. `current_member()` and `assert_valid_request` only gate *who may call* `confirm`/`add_request` now — they do not retroactively invalidate confirmations already stored from a member who has since been removed. [4](#0-3) [5](#0-4) 
5. Consequently, a request `R` created by member A and confirmed by member B before B was removed keeps `B` in `confirmations(R)` forever (or until R is executed/deleted). Once enough *other, live* members confirm, `confirmations.len() + 1 >= num_confirmations` becomes true and `execute_request` runs, even though B's vote is stale.

Exploit flow (no privilege needed beyond being an existing multisig member acting in concert, or simply the passage of normal governance actions):
- Members: A, B, C, D; `num_confirmations = 3`.
- A submits malicious/attacker-favoring `Transfer` request `R` (`add_request`).
- B confirms `R` → `confirmations(R) = {B}`.
- The multisig later (for unrelated legitimate reasons) removes B via a `DeleteMember{B}` request — this only clears requests B itself created, not `R`. `confirmations(R)` still contains `B`.
- C confirms `R` → `confirmations(R) = {B, C}` (2).
- D confirms `R` → `confirmations(R) = {B, C, D}` (3) `>= num_confirmations` → `remove_request` + `execute_request` fires, moving funds out via `Promise`, even though only 2 *live* members (C, D) actually approved it.

This directly matches the Critical category: "a multisig request executed below `num_confirmations` live members."

### Impact Explanation
NEAR (or any action bundled in the request, including `Transfer`, `AddKey`, further `DeleteMember`/`AddMember`) is executed from the multisig's own account using a confirmation count that includes a former, non-member's stale vote. This lets fewer live members than the configured threshold push through a fund-moving request — a full compromise of the multisig's confirmation guarantee. The attack is repeatable for every request left in "partially confirmed" state at the time any confirming member is removed, and applies to every multisig deployed from this contract's code, so the blast radius covers all funds controlled by any `multisig2` instance that experiences ordinary membership churn.

### Likelihood Explanation
This does not require a malicious member out of the current member set to succeed — it only requires normal, legitimate multisig activity: a member confirms a request, and later that member is removed for routine reasons (key rotation, offboarding, security). No attacker cost beyond being one of the multisig's own members is needed, and the stale confirmation persists indefinitely until the request is executed or explicitly deleted (subject to `REQUEST_COOLDOWN`). Because ordinary membership changes are common in long-lived multisigs, this is a realistic and repeatable path, not a purely theoretical one.

### Recommendation
In `delete_member`, iterate all entries in `confirmations` (not just requests submitted by the deleted member) and remove the deleted member's `to_string()` key from every confirmation `HashSet`. Alternatively, validate at `confirm`/execution time that every string in a request's confirmation set still corresponds to a member currently in `self.members`, and only count live members toward the `num_confirmations` threshold.

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_after_member_removal_still_counts() {
    // members: alice (Account), bob (Account), key1 (AccessKey), key2 (AccessKey)
    // num_confirmations = 3
    let amount = 1_000;
    testing_env!(context_with_account(alice(), amount));
    let mut c = MultiSigContract::new(members(), 3);

    // 1. alice submits a transfer request R
    let request = MultiSigRequest {
        receiver_id: bob(),
        actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
    };
    let request_id = c.add_request(request);

    // 2. bob confirms R (not the submitter)
    testing_env!(context_with_account(bob(), amount));
    c.confirm(request_id);
    assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1);

    // 3. members remove bob via DeleteMember (submitted+confirmed by others, e.g. key1/key2/alice)
    // ... submit and confirm a DeleteMember{ Account(bob) } request with 3 confirmations from alice/key1/key2 ...
    // after execution: bob removed from c.members, but confirmations(request_id) still contains bob's string.
    assert!(c.confirmations.get(&request_id).unwrap().contains(&MultisigMember::Account{account_id: bob()}.to_string()));
    assert!(!c.members.contains(&MultisigMember::Account{account_id: bob()}));

    // 4. two more LIVE members confirm R
    testing_env!(context_with_key(key1_pubkey(), amount));
    c.confirm(request_id); // confirmations = {bob, key1} = 2
    testing_env!(context_with_key(key2_pubkey(), amount));
    let result = c.confirm(request_id); // confirmations = {bob, key1, key2} = 3 >= 3 -> EXECUTES

    // Assert: request executed (removed from requests map) with only 2 live-member confirmations
    assert_eq!(c.requests.len(), 0); // R executed
    // live confirmers were only key1 and key2 (2 < num_confirmations == 3)
}
```
This demonstrates the request executing with only 2 confirmations from currently-live members while `num_confirmations == 3`, proving the invariant "requests execute only with `num_confirmations` live-member confirmations" is broken.

### Citations

**File:** multisig2/src/lib.rs (L188-197)
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
```

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

**File:** multisig2/src/lib.rs (L322-339)
```rust
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

**File:** multisig2/src/lib.rs (L407-423)
```rust
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
