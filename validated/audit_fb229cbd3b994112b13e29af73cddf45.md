### Title
Confirm() counts stale confirmations from removed members, letting a request execute below `num_confirmations` live members - (File: `multisig2/src/lib.rs`)

### Summary
The threshold check in `confirm()` compares `confirmations.len() as u32 + 1` against `self.num_confirmations`, where `confirmations` is a `HashSet<String>` of member identity strings persisted per request. `delete_member()` only purges confirmations for requests that a removed member *originally submitted*, not requests that member merely *confirmed*, so a confirmation string from a since-removed member survives and is counted toward quorum. A `Transfer` request can therefore execute after fewer than `num_confirmations` *current* members actually approved it.

### Finding Description
The intended invariant is: `confirmations_from_current_members.len() >= self.num_confirmations`. The actual code implements: `confirmations.len() (stored strings, possibly stale) + 1 >= self.num_confirmations`, with no check that every stored confirmer is still in `self.members`.

Concretely:
- `confirm()` reads `self.confirmations.get(&request_id)` and tests `confirmations.len() as u32 + 1 >= self.num_confirmations` [1](#0-0) , then either executes the request via `execute_request` or inserts the new confirmer's `member.to_string()` [2](#0-1) .
- `assert_valid_request` only checks that the *caller* is a current member and that the request/confirmations entries exist; it never re-validates that the *stored* confirmation strings correspond to current members [3](#0-2) .
- `delete_member` cleans up confirmations only for requests where `r.member == member`, i.e. requests *originated* by the removed member: `self.requests.iter().filter_map(|(k, r)| if r.member == member { Some(k) } else { None })`, then removes those requests' confirmations [4](#0-3) . It does **not** scan `confirmations` for entries where the removed member merely *confirmed* someone else's request.

Exploit flow (all steps performed through the contract's own normal member operations, no outside key or privilege beyond that already granted to multisig members):
1. Multisig has members `{A, B, C}`, `num_confirmations = 2`.
2. Member `B` submits request `R1`: `Transfer { amount }` to an external `receiver_id` (any account, e.g. one the attacker controls) - `Transfer` is not gated by `assert_self_request`, unlike `AddMember`/`DeleteMember`/`AddKey` [5](#0-4) .
3. Member `A` calls `confirm(R1)`. Since `confirmations.len()+1 = 1 < 2`, `A`'s id string is inserted into `confirmations` [6](#0-5) .
4. Members `B`/`C` submit and confirm a `DeleteMember { member: A }` request (allowed since `self.members.len() - 1 = 2 >= num_confirmations = 2` passes the assert at line 358) [7](#0-6) . Because `R1`'s original submitter was `B`, not `A`, `R1`'s confirmations are untouched by the cleanup loop, so `A`'s stale confirmation string remains in `confirmations` for `R1`.
5. Members are now `{B, C}`, threshold is still 2. `C` calls `confirm(R1)`. The check computes `confirmations.len() (=1, stale "A") + 1 = 2 >= num_confirmations (=2)` → true, and `execute_request` fires the `Transfer` [8](#0-7) .

Only one currently-live member (`C`) actually approved the transfer at execution time, yet the contract enforced a "2-of-2" quorum using a ghost confirmation from a member who was no longer part of the multisig. This breaks the stated invariant that the count compared against `num_confirmations` must reflect only current members.

### Impact Explanation
Funds (`Transfer` amount) leave the multisig account to a `receiver_id` that was never actually approved by the required number of *live* members - this is the Critical category "a multisig request executed below `num_confirmations` live members" / "NEAR moved out of a multisig by a party not entitled to it." The blast radius covers any `multisig2` deployment where membership changes occur while confirmed-but-not-yet-executed requests are outstanding; it is repeatable for every request/membership-change combination that exhibits the pattern, and severity scales with the transferred amount and the number of stale ghost confirmations accumulated.

### Likelihood Explanation
No special privilege beyond normal, expected multisig usage is required: a request submission, one confirmation, a routine member removal, and one more confirmation - actions every multisig performs regularly. The only precondition is that a member who previously confirmed an outstanding request is later removed before that request is executed or deleted, which is a realistic and even likely occurrence (e.g., removing a compromised or departing key holder). The `REQUEST_COOLDOWN` and `ACTIVE_REQUESTS_LIMIT` do not prevent this since the request remains "active" the whole time. Cost to trigger is a handful of ordinary transactions; no economic outlay is needed.

### Recommendation
When counting confirmations in `confirm()`, filter `confirmations` to only members currently present in `self.members` before comparing against `self.num_confirmations` (e.g., `confirmations.iter().filter(|c| self.members.contains(&member_from_string(c))).count()`), or proactively purge confirmation entries for any member string whenever that member is deleted in `delete_member`, scanning all requests' confirmation sets (not just requests they originated) and removing the member's string from each.

### Proof of Concept
```rust
// multisig2/src/lib.rs - add to `mod tests`
#[test]
fn test_stale_confirmation_counts_after_member_removal() {
    // members: alice (account), bob (account), plus 2 access-key members from members()
    // use num_confirmations = 2 for a 3-member subset scenario analogous to the report
    let amount = 1_000;
    testing_env!(context_with_account(bob(), amount));
    let mut c = MultiSigContract::new(
        vec![
            MultisigMember::Account { account_id: alice() },
            MultisigMember::Account { account_id: bob() },
            MultisigMember::AccessKey {
                public_key: PublicKey::try_from(TEST_KEY.to_vec()).unwrap(),
            },
        ],
        2,
    );

    // bob submits a transfer request to an external account
    let request = MultiSigRequest {
        receiver_id: alice(), // stand-in for an outside receiver
        actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
    };
    let request_id = c.add_request(request);

    // access-key member confirms it (1 confirmation, below threshold of 2)
    testing_env!(context_with_key(
        PublicKey::try_from(TEST_KEY.to_vec()).unwrap(),
        amount
    ));
    c.confirm(request_id);
    assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1);

    // members remove the access-key member via DeleteMember (2 confirmations from alice/bob)
    testing_env!(context_with_account(alice(), amount));
    let delete_req_id = c.add_request(MultiSigRequest {
        receiver_id: env::current_account_id(),
        actions: vec![MultiSigRequestAction::DeleteMember {
            member: MultisigMember::AccessKey {
                public_key: PublicKey::try_from(TEST_KEY.to_vec()).unwrap(),
            },
        }],
    });
    c.confirm(delete_req_id);
    testing_env!(context_with_account(bob(), amount));
    c.confirm(delete_req_id);

    // BINDING CHECK: the removed member's confirmation on `request_id` should be gone
    // if the invariant "confirmations reflect only current members" holds.
    // This assertion FAILS today, proving the stale entry survives:
    assert!(
        c.confirmations.get(&request_id).unwrap().is_empty(),
        "stale confirmation from removed access-key member still present"
    );

    // Now only alice and bob remain, num_confirmations is still 2.
    // bob confirms the original transfer request: only ONE live member (bob) has
    // ever confirmed it, but the stale entry pushes it over threshold and executes it.
    testing_env!(context_with_account(bob(), amount));
    c.confirm(request_id);
    assert_eq!(c.requests.len(), 0); // request executed with only 1 live confirmation
}
```
Run with `cargo test -p multisig2 test_stale_confirmation_counts_after_member_removal`. The `assert!` on the emptied confirmation set fails, demonstrating that `confirmations.len() as u32 + 1 >= self.num_confirmations` [9](#0-8)  counted a non-member's stale entry, and the subsequent `confirm` call executes the request despite only one currently-live confirmation.

### Citations

**File:** multisig2/src/lib.rs (L224-242)
```rust
    fn execute_request(&mut self, request: MultiSigRequest) -> PromiseOrValue<bool> {
        let mut promise = Promise::new(request.receiver_id.clone());
        let receiver_id = request.receiver_id.clone();
        let num_actions = request.actions.len();
        for action in request.actions {
            promise = match action {
                MultiSigRequestAction::Transfer { amount } => promise.transfer(amount.into()),
                MultiSigRequestAction::CreateAccount => promise.create_account(),
                MultiSigRequestAction::DeployContract { code } => {
                    promise.deploy_contract(code.into())
                }
                MultiSigRequestAction::AddMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.add_member(promise, member)
                }
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
                }
```

**File:** multisig2/src/lib.rs (L299-315)
```rust
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

**File:** multisig2/src/lib.rs (L356-360)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
```

**File:** multisig2/src/lib.rs (L361-371)
```rust
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
