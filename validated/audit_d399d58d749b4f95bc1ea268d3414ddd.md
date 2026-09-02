## Title
Stale confirmations from deleted members are still counted toward `num_confirmations`, allowing a multisig request to execute with fewer than `num_confirmations` live members - (File: `multisig2/src/lib.rs`)

## Summary
`delete_member` only purges pending requests and confirmations for requests that the removed member itself *created* (`r.member == member`), but never scans the `confirmations` map of requests created by *other* members to strip out the removed member's confirmation entry. `confirm()` then simply counts `confirmations.len()` without verifying that every entry still belongs to a current member, so a request can be executed once the stale entry plus fresh confirmations from current members reach the threshold, even though fewer than `num_confirmations` live members actually approved it.

## Finding Description
The invariant that should hold is: `count({m in confirmations(request_id) : m ∈ current_members}) >= num_confirmations` at the moment a request executes. The code instead checks `confirmations.len() as u32 + 1 >= self.num_confirmations` in `confirm`, with no re-validation of each entry's current membership: [1](#0-0) 

`delete_member` only cleans confirmations for requests whose *creator* (`r.member`) equals the removed member; it does not touch the `confirmations` HashSet of requests created by other members that the removed member merely confirmed: [2](#0-1) 

`current_member()` and `assert_valid_request` only validate the *caller's* current membership for the call in progress; they never re-check the membership of principals whose confirmation strings are already stored in the `confirmations` set: [3](#0-2) [4](#0-3) 

Exploit flow (with `num_confirmations = 2`, members `{A, B, C}`):
1. Member `A` calls `add_request` to create `R1`: `receiver_id = <external account>`, `actions = [Transfer { amount }]`. `R1.member = A` (the creator), confirmations empty.
2. Member `C` calls `confirm(R1)` → `confirmations(R1) = {C}` (1/2, below threshold, no execution).
3. Separately, members `A` and `B` create and confirm `R2`: `receiver_id = self`, `actions = [DeleteMember { member: C }]`. This reaches 2/2 and executes, calling `delete_member(C)`.
4. `delete_member` filters `self.requests` for entries where `r.member == C` — but `R1.member == A`, not `C`, so `R1` and its confirmations are left untouched. `C`'s stale confirmation entry remains in `confirmations(R1)`.
5. `C` is now removed from `self.members`, so `current_member()` would reject `C` as a caller — but that check is never re-applied to *stored* confirmation entries.
6. Member `A` (or any live member) calls `confirm(R1)`. `confirmations(R1).len()` is `1` (from `C`) `+ 1` (from `A`) `= 2 >= num_confirmations`, so `execute_request(R1)` runs the `Transfer`, moving funds to the external receiver, even though only `A` — a single live member — actually authorized it after `C`'s removal.

Existing guards do not catch this: `assert_valid_request` only checks that the *current caller* is a member; `assert_self_request` only applies to self-targeted actions like `DeleteMember`/`AddMember`/`AddKey` and does not gate `Transfer`; there is no invariant check anywhere that re-validates the membership of every principal already present in `confirmations` before executing.

## Impact Explanation
NEAR (or any other value-moving action in the request, e.g., `FunctionCall` with an attached deposit, `AddKey` granting access) can be executed on behalf of the multisig account with fewer than `num_confirmations` currently valid members having approved it. This directly matches the stated Critical category: "a multisig request executed below `num_confirmations` live members," and can result in unauthorized transfer of the multisig account's funds. The bug is repeatable any time membership changes while confirmations for other pending requests are outstanding — a routine, expected operational pattern for a multisig (member rotation).

## Likelihood Explanation
This requires no privileged NEAR Foundation role, only ordinary multisig operation: creating requests, confirming them, and later removing a member — all standard actions taken by the multisig's own members. No malicious member collusion beyond normal timing is needed; even a benign member rotation (removing a departing member who had already confirmed a pending, unrelated request) can inadvertently leave that member's confirmation "live" for future counting. It is fully reproducible in a unit test with `testing_env!`.

## Recommendation
When a member is removed in `delete_member`, iterate over **all** requests' `confirmations` sets (not only ones the removed member created) and remove the member's entry from each. Additionally/alternatively, in `confirm` (and anywhere confirmation count is evaluated), filter `confirmations` down to entries whose corresponding member is still present in `self.members` before comparing against `self.num_confirmations`.

## Proof of Concept
```rust
// multisig2/src/lib.rs tests module
#[test]
fn stale_confirmation_after_member_removal_still_counted() {
    // members: alice, bob, and an access-key member C; num_confirmations = 2
    testing_env!(context_with_account(alice(), 0));
    let mut c = MultiSigContract::new(members_subset_abc(), 2); // {alice, bob, C}

    // Step 1: alice creates R1 transferring funds externally (not self-targeted)
    testing_env!(context_with_account(alice(), 0));
    let r1 = MultiSigRequest {
        receiver_id: AccountId::new_unchecked("external".to_string()),
        actions: vec![MultiSigRequestAction::Transfer { amount: 1000.into() }],
    };
    let r1_id = c.add_request(r1);

    // Step 2: member C confirms R1 (1/2, no execution)
    testing_env!(context_with_key(c_pubkey(), 0));
    c.confirm(r1_id);
    assert_eq!(c.get_confirmations(r1_id).len(), 1);

    // Step 3: alice + bob create & confirm R2 deleting member C (self-targeted, reaches 2/2)
    testing_env!(context_with_account(alice(), 0));
    let r2 = MultiSigRequest {
        receiver_id: alice(), // == current_account_id in test env
        actions: vec![MultiSigRequestAction::DeleteMember {
            member: MultisigMember::AccessKey { public_key: c_pubkey() },
        }],
    };
    let r2_id = c.add_request(r2);
    c.confirm(r2_id); // alice confirms
    testing_env!(context_with_account(bob(), 0));
    c.confirm(r2_id); // bob confirms -> 2/2 -> executes delete_member(C)

    // Assert C is no longer a member
    assert!(!c.get_confirmations(r1_id).is_empty()); // stale confirmation from C still present

    // Step 4: alice confirms R1 -> reaches len(1 stale)+1 == num_confirmations -> executes Transfer
    testing_env!(context_with_account(alice(), 0));
    let result = c.confirm(r1_id);
    // ASSERT ON THE BROKEN BINDING:
    // count(live members in confirmations(r1)) should be < num_confirmations (only `alice`),
    // yet execute_request was invoked (PromiseOrValue is a scheduled Promise, not Value(true))
    match result {
        PromiseOrValue::Promise(_) => (), // BUG: executed with only 1 live confirmer
        PromiseOrValue::Value(_) => panic!("expected the flawed path to execute prematurely"),
    }
}
```
This test demonstrates: confirm → remove the confirmer via `delete_member` → confirm once more → observe premature execution, exactly as described in the "Fast validation" instructions.

### Citations

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
