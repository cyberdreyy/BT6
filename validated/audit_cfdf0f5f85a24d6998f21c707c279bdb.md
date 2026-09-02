### Title
THRESHOLD violation via stale confirmation from a removed multisig member - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only removes requests that were *created* by the removed member, and only clears that member's `num_requests_pk` entry; it never scans `self.confirmations` to strip that member's confirmation from *other* still-open requests they had previously confirmed. Consequently a stale confirmation string from a member who is no longer in `self.members` remains counted by `confirm`'s threshold check (`confirmations.len() as u32 + 1 >= self.num_confirmations`), letting a request execute (e.g. a `Transfer`) with fewer live, currently-authorized confirmers than `num_confirmations`.

### Finding Description
The invariant that must hold is: `|{m in self.members : m confirmed request R}| == num_confirmations` at the moment `R` executes. The code instead checks `self.confirmations.get(&request_id).len() + 1 >= self.num_confirmations` in `confirm` [1](#0-0) , where `confirmations` is a `HashSet<String>` of `member.to_string()` values recorded at confirmation time, with no re-validation against current `self.members`.

`delete_member` removes a member's own submitted requests and their `num_requests_pk` counter, but filters requests by `r.member == member` — i.e., requests the removed member *authored*, not requests they merely *confirmed*: [2](#0-1) 

Because of this, if member `B` confirms a request `R1` created by `A`, and `B` is later removed via a separate `DeleteMember{B}` request (itself executed with 3 valid confirmations from `A`, `C`, `D`), `R1`'s confirmation set still contains `B`'s serialized identity. When a fourth live member (`C`) later calls `confirm(R1)`, the check `confirmations.len() as u32 + 1 >= num_confirmations` (2 stale/live + 1 = 3 >= 3) passes and `execute_request` fires the `Transfer`, even though only `A` and `C` are still live members who confirmed — one confirmation slot was satisfied by a removed member `B`.

None of the existing guards catch this: `assert_valid_request` only checks the caller is a current member and that the request/confirmations exist [3](#0-2) ; it does not re-validate that every entry in the stored confirmations set still belongs to `self.members`.

### Impact Explanation
NEAR is moved out of the multisig account via `Promise::transfer` in `execute_request` [4](#0-3)  to `receiver_id` even though fewer than `num_confirmations` currently-live members actually authorized it. This is a Critical-severity finding per the rules: "a multisig request executed below `num_confirmations` live members." The attack is repeatable for any multisig instance created by the public factory as long as an attacker can arrange (or wait for) a member removal after partial confirmations exist on an open request — every subsequent removal event leaves stale confirmations on any request the removed member touched but did not create.

### Likelihood Explanation
Requires: a multisig with `num_confirmations >= 3` (or generally `>= 2`) and at least one pending request that has been confirmed by a member who is later removed via `DeleteMember` while that request remains open (not deleted/re-confirmed to completion first). This can arise organically (e.g., member rotation/off-boarding while a transfer is mid-approval) or be deliberately engineered by a member colluding with the deletion proposal timing. No special privileges beyond being one of the existing members are needed to trigger the final `confirm` call; the flaw is purely in `delete_member`'s cleanup logic, not in any single call being made by an "unprivileged" outsider — the exploitation path is fully internal to the multisig membership set already holding legitimate confirm rights.

### Recommendation
In `delete_member`, iterate over all entries in `self.requests` (not just those authored by the removed member) and remove the removed member's `to_string()` entry from every corresponding `self.confirmations` set. Alternatively, have `confirm` re-validate that every string in the stored confirmations set still corresponds to a member in `self.members` before counting it toward the threshold (filtering out stale entries, and persisting the filtered set back).

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_after_member_removal() {
    // members(): [alice(Account), bob(Account), AccessKey(Eg2...), AccessKey(TEST_KEY)]
    // Treat alice=A, bob=B, key1=C, key2=D. num_confirmations = 3.
    let amount = 1_000;
    testing_env!(context_with_account(alice(), amount));
    let mut c = MultiSigContract::new(members(), 3);

    // Step 1: A creates Transfer request R1
    let transfer_req = MultiSigRequest {
        receiver_id: bob(),
        actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
    };
    let r1 = c.add_request(transfer_req.clone());
    c.confirm(r1); // A confirms -> confirmations = {A}

    // Step 2: B confirms R1 -> confirmations = {A, B}, count = 2 < 3, still pending
    testing_env!(context_with_account(bob(), amount));
    c.confirm(r1);
    assert_eq!(c.confirmations.get(&r1).unwrap().len(), 2);

    // Step 3: Remove B via its own DeleteMember request, confirmed by A, C, D (3 confirmations)
    let bob_member = MultisigMember::Account { account_id: bob() };
    testing_env!(context_with_account(alice(), amount));
    let del_req = c.add_request(MultiSigRequest {
        receiver_id: alice(), // current_account_id
        actions: vec![MultiSigRequestAction::DeleteMember { member: bob_member.clone() }],
    });
    c.confirm(del_req); // A
    testing_env!(context_with_key(PublicKey::from("ed25519:Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy".parse().unwrap()), amount));
    c.confirm(del_req); // C
    testing_env!(context_with_key(PublicKey::try_from(TEST_KEY.to_vec()).unwrap(), amount));
    c.confirm(del_req); // D -> executes delete_member(bob)

    // Binding check BEFORE: bob removed from members
    assert!(!c.members.contains(&bob_member));
    // R1's confirmations set STILL contains bob's stale entry (not purged)
    assert!(c.confirmations.get(&r1).unwrap().contains(&bob_member.to_string()));

    // Step 4: C (still live) confirms R1 -> triggers execution with only A,C live + stale B
    testing_env!(context_with_key(PublicKey::from("ed25519:Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy".parse().unwrap()), amount));
    c.confirm(r1); // executes Transfer via execute_request

    // Binding VIOLATED: request executed (removed from map) though only 2 live members (A, C) confirmed,
    // not num_confirmations (3), because bob's stale confirmation was still counted.
    assert_eq!(c.requests.get(&r1), None); // request executed/removed
}
```
This demonstrates that `execute_request` fires the `Transfer` for `r1` with the threshold satisfied only by counting a confirmation from `bob`, a member no longer present in `self.members`, proving the broken binding `|live confirmers| == num_confirmations`.

### Citations

**File:** multisig2/src/lib.rs (L224-230)
```rust
    fn execute_request(&mut self, request: MultiSigRequest) -> PromiseOrValue<bool> {
        let mut promise = Promise::new(request.receiver_id.clone());
        let receiver_id = request.receiver_id.clone();
        let num_actions = request.actions.len();
        for action in request.actions {
            promise = match action {
                MultiSigRequestAction::Transfer { amount } => promise.transfer(amount.into()),
```

**File:** multisig2/src/lib.rs (L299-309)
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
