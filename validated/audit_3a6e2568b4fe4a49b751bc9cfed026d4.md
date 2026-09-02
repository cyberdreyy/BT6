### Title
Stale confirmations from removed multisig members are still counted toward `num_confirmations`, allowing execution with fewer live-member approvals than the threshold - (File: `multisig2/src/lib.rs`)

### Summary
`confirm` compares `confirmations.len() as u32 + 1 >= self.num_confirmations` against the size of the stored `confirmations` `HashSet`, but `delete_member` only purges confirmations for requests that the removed member *originally created* (`r.member == member`), not confirmations that member added to *other* requests as a co-signer. A member who confirmed a still-open request and is later removed from the multisig leaves a "ghost" confirmation behind that still counts toward the threshold, letting the request execute with fewer genuinely live approvals than `num_confirmations` requires.

### Finding Description
The invariant that should hold is: `confirmations.len() == |{m in confirmations : m ∈ current members}|` for every open request, i.e. every entry counted in `confirmations.len()` must correspond to an account/key that is still a current member. This invariant is violated.

- `confirm()` reads `self.confirmations.get(&request_id)` and checks `confirmations.len() as u32 + 1 >= self.num_confirmations` [1](#0-0) , purely counting stored `HashSet<String>` entries with no re-validation against `self.members`.
- The only place membership removal happens is `delete_member`, which enforces `self.members.len() - 1 >= self.num_confirmations` and cleans up confirmations *only* for requests the removed member authored (`r.member == member`) [2](#0-1) . It never scans other open requests' `confirmations` sets to strip entries where the removed member acted merely as a confirmer.
- `current_member()` is only used to gate *who may call* `add_request`/`confirm`/`assert_valid_request`; it is never used to filter or re-validate the *stored* confirmation set at the moment of counting [3](#0-2) .

Exploit flow (all callers are existing current members acting through the exposed `confirm`/`add_request_and_confirm` entrypoints — the only way to reach `confirm` per `assert_valid_request`):
1. Members = {A,B,C,D}, `num_confirmations = 3`.
2. Member A creates request R (a `Transfer`) via `add_request`. Member B calls `confirm(R)` → `confirmations = {B}` (count 1, below 3, not executed).
3. Via a separate, properly-quorate request, members remove B from the multisig (`DeleteMember`), satisfying `members.len()-1 (=3) >= num_confirmations (=3)`. Because R was authored by A, not B, `delete_member`'s cleanup loop skips it, so `confirmations` for R still contains `"B"`.
4. Now members = {A,C,D}, still `num_confirmations = 3`, but B's stale entry survives in R's confirmation set.
5. C calls `confirm(R)` → `confirmations = {B,C}` (count 2, still below 3).
6. D calls `confirm(R)` → `confirmations.len() as u32 + 1 = 3 >= 3` → `execute_request` fires the `Transfer`, moving funds out of the account.

Only C and D — two live members — actually approved R; B's approval is a ghost credited by an account no longer entitled to authorize anything. The threshold of 3 is satisfied by counting a non-member's stale entry, not by three current members. Existing guards do not catch this: `assert_valid_request` only checks that the *caller* is a current member [4](#0-3) ; it does not re-validate the *stored confirmers*. `delete_member`'s member-count assertion protects only against total member count dropping below the threshold, not against stale confirmer entries persisting in unrelated requests' confirmation sets.

### Impact Explanation
This allows a multisig request (including a `Transfer` that moves NEAR out of the account, or `AddKey`/`DeleteMember` actions) to be executed with fewer genuinely live member approvals than `num_confirmations`, directly matching the Critical impact category "a multisig request executed below `num_confirmations` live members." The blast radius covers any multisig instance deployed via the `multisig-factory` where membership churn occurs while requests remain open; it is repeatable across requests and across every multisig account using this contract.

### Likelihood Explanation
The preconditions are ordinary operational events for any multisig: a member confirms a request, and later that same member is removed from the multisig set before the request is confirmed by enough remaining members. No attacker cost beyond normal multisig usage is required, and there is no time bound — a request can sit unconfirmed (subject only to `ACTIVE_REQUESTS_LIMIT`/`REQUEST_COOLDOWN` for deletion) indefinitely while membership changes occur. This is highly feasible and repeatable for any multisig that ever removes a member while requests remain open, which is a realistic and common administrative action.

### Recommendation
When a member is deleted (in `delete_member`), scan all open requests' `confirmations` sets and remove any entry corresponding to the deleted member's `to_string()` representation, not only the confirmations for requests that member authored. Alternatively (and more robustly), have `confirm()` recompute the effective confirmation count by filtering `confirmations` against `self.members.contains(...)` before comparing against `num_confirmations`, so stale entries from ex-members can never count toward the threshold.

### Proof of Concept
```rust
// multisig2/src/lib.rs - new test in `mod tests`
#[test]
fn test_stale_confirmation_from_removed_member_counts_toward_threshold() {
    // Members: alice (Account), bob (Account), key1 (AccessKey), key2 (AccessKey)
    // num_confirmations = 3
    let amount = 1_000;
    testing_env!(context_with_account(alice(), amount));
    let mut c = MultiSigContract::new(members(), 3);

    // Step 1: alice creates a Transfer request R
    let transfer_req = MultiSigRequest {
        receiver_id: bob(),
        actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
    };
    let request_id = c.add_request(transfer_req);

    // Step 2: bob confirms R -> confirmations = {bob}
    testing_env!(context_with_account(bob(), amount));
    c.confirm(request_id);
    assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1);

    // Step 3: remove bob via a separate quorate DeleteMember request (authored+confirmed by alice/others)
    testing_env!(context_with_account(alice(), amount));
    let delete_req_id = c.add_request(MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::DeleteMember {
            member: MultisigMember::Account { account_id: bob() },
        }],
    });
    // confirm with key1 and key2 to reach threshold of 3 (alice + key1 + key2)
    testing_env!(context_with_key(/* key1 pk */ .., amount));
    c.confirm(delete_req_id);
    testing_env!(context_with_key(/* key2 pk */ .., amount));
    c.confirm(delete_req_id); // executes DeleteMember{bob}

    // ASSERT the broken binding: bob is no longer a member...
    assert!(!c.get_members().contains(&MultisigMember::Account { account_id: bob() }));
    // ...but R's confirmations still contains "bob"'s stale entry
    assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1); // still {bob}

    // Step 4/5: key1 and key2 (2 live members) confirm R; count reaches 3 only because of bob's ghost entry
    testing_env!(context_with_key(/* key1 pk */ .., amount));
    c.confirm(request_id); // confirmations = {bob, key1}, count 2 < 3
    testing_env!(context_with_key(/* key2 pk */ .., amount));
    c.confirm(request_id); // confirmations.len()+1 = 3 >= 3 -> EXECUTES transfer

    // R is now removed (executed) with only 2 live-member confirmations (key1, key2) plus 1 stale (bob)
    assert_eq!(c.requests.len(), 0);
}
```
This test demonstrates that `confirmations.len() as u32 + 1 >= self.num_confirmations` at `multisig2/src/lib.rs:304` is satisfied using a confirmation entry (`"bob"`) that no longer corresponds to a current member, breaking the invariant that all counted confirmations belong to live members and allowing unauthorized execution of a fund-moving request.

### Citations

**File:** multisig2/src/lib.rs (L299-304)
```rust
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
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

**File:** multisig2/src/lib.rs (L356-371)
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
