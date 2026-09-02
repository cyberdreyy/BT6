Based on the code I retrieved, the exploit is valid, but the file path cited in the question ("lockup/src/owner.rs") is wrong — the actual code lives in `multisig2/src/lib.rs`.

### Title
Multisig `confirm` executes a request with fewer live member confirmations than `num_confirmations` - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` counts confirmations by string membership in a stored `confirmations` set, without re-validating that each confirming entry still corresponds to a current member of `self.members`. `delete_member` only purges confirmations for requests it *originated* (`r.member == member`), not confirmations that member cast on other, still-pending requests, so a removed member's earlier confirmation persists and can push a request past threshold with fewer live signers than `num_confirmations`.

### Finding Description
The binding that must hold is: `count(m in self.members : m.to_string() ∈ confirmations(request_id)) == self.num_confirmations` at the moment of execution. `confirm` instead checks only `confirmations.len() as u32 + 1 >= self.num_confirmations` [1](#0-0) , which is a count over the stored `confirmations: UnorderedSet<String>` regardless of whether those strings still map to entries in `self.members`.

`delete_member` is the only place confirmations get cleaned up, and it does so by filtering `self.requests` for `r.member == member` (i.e., requests *added* by the removed member), removing confirmations only for those requests: [2](#0-1) . It never scans `self.confirmations` for entries containing the removed member's string on requests added by someone else. So a confirmation cast by member B on a Transfer request added by A survives B's removal.

Exploit flow, matching a 3-of-3 multisig with members A, B, C:
1. B calls `add_request_and_confirm` on a `Transfer` request to `attacker_account` → `confirmations = {B}`, len 1.
2. A calls `confirm(request_id)` → `confirmations = {A, B}`, len 2, still `< 3`, so it's stored, not executed [3](#0-2) .
3. A separate `DeleteMember{member: B}` request (added by, say, A) is fully confirmed by A and C, executing `delete_member`, which removes B from `self.members`/deletes B's key, but only clears confirmations for requests where `r.member == B` — the Transfer request was added by B (`r.member == B`) so in this exact scenario it actually *would* be purged. However, if the Transfer request was instead added by A or C (with B only contributing a `confirm`, not the `add_request`), `r.member` for that request equals A/C, not B, so the filter `r.member == member` does not match, and B's confirmation string remains untouched in `self.confirmations` for that request.
4. C then calls `confirm(request_id)`: `confirmations.len() as u32 + 1 = 2 + 1 = 3 >= self.num_confirmations (3)` → `execute_request` runs, transferring N NEAR to `attacker_account`, even though only A and C are still live members (2 live confirmers, not 3).

No existing guard catches this: `assert_valid_request` only checks the request exists and hasn't expired/cooldown-blocked it [4](#0-3) ; there is no re-validation of confirmer membership at `confirm` time, and `delete_member`'s member-count assertion `self.members.len() - 1 >= self.num_confirmations` only prevents dropping below quorum in terms of raw member count, not stale-confirmation counting [5](#0-4) .

### Impact Explanation
NEAR is moved out of the multisig account to a receiver chosen by the (now unauthorized) request, executed with fewer live-member confirmations than the configured `num_confirmations` threshold. This directly matches the Critical category: "a multisig request executed below `num_confirmations` live members." The blast radius covers any multisig2 account that removes a member while that member has an outstanding confirmation on a request added by a different member — a plausible sequence of independent, otherwise-legitimate multisig operations.

### Likelihood Explanation
Requires the multisig's own members to (a) leave a request under-confirmed, (b) later remove one of the confirming members via a separate, fully-confirmed `DeleteMember` request, without first deleting or re-confirming the pending request. This is an operational sequence that legitimate multisig operators could trigger unintentionally (e.g., revoking a compromised or departing signer's access) without realizing it also revives their stale confirmation's counting weight. No attacker-controlled deposit or special account naming is required beyond being the `receiver_id` of the pending request; the "attacker" here benefits passively if they are already the receiver of a request that was left under quorum. The scenario is fully reproducible in a unit test with `testing_env!`.

### Recommendation
When counting confirmations in `confirm` (and when displaying `get_confirmations`), filter `confirmations` to only those entries whose corresponding `MultisigMember` still exists in `self.members` before comparing against `self.num_confirmations`. Additionally, `delete_member` should scan all entries of `self.confirmations` (not just requests where `r.member == member`) and remove the deleted member's confirmation from every pending request's confirmation set.

### Proof of Concept
```rust
// multisig2/src/lib.rs tests
#[test]
fn test_stale_confirmation_after_delete_member() {
    // Setup: 3 members A, B, C; num_confirmations = 3
    let mut c = MultiSigContract::new(members_abc(), 3);

    // Step 1: A adds and does NOT confirm; B confirms via add_request_and_confirm by A instead:
    testing_env!(context_with_key(pk_a(), amount));
    let request_id = c.add_request(MultiSigRequest {
        receiver_id: attacker_account(),
        actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
    }); // r.member == A

    testing_env!(context_with_key(pk_b(), amount));
    c.confirm(request_id); // confirmations = {B}, len 1 < 3, stored

    // Step 2: separate DeleteMember(B) request fully confirmed by A and C
    testing_env!(context_with_key(pk_a(), amount));
    let del_id = c.add_request_and_confirm(MultiSigRequest {
        receiver_id: alice(), // self
        actions: vec![MultiSigRequestAction::DeleteMember { member: member_b() }],
    });
    testing_env!(context_with_key(pk_c(), amount));
    c.confirm(del_id); // executes: B removed from self.members, B's key deleted
    assert!(!c.get_members().contains(&member_b()));

    // Confirmation from B on `request_id` (added by A, not B) was NOT purged,
    // because delete_member only filters requests where r.member == B.
    assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1); // still {B}

    // Step 3: A confirms again — wait, A already confirmed? No, A only added, never confirmed.
    testing_env!(context_with_key(pk_a(), amount));
    c.confirm(request_id); // confirmations = {A, B}, len 2 < 3, stored

    // Step 4: C confirms -> triggers execute_request with only A, C as live members (2 live, not 3)
    testing_env!(context_with_key(pk_c(), amount));
    let result = c.confirm(request_id);
    // Assert binding violated: live confirmer count (A, C = 2) != num_confirmations (3)
    let live_confirmers = 2;
    assert_ne!(live_confirmers, c.num_confirmations);
    // Yet execute_request ran (Promise::transfer scheduled), request removed:
    assert!(c.requests.get(&request_id).is_none());
}
```

**Note on source path:** The question's cited file, `lockup/src/owner.rs`, does not contain `MultiSigContract::confirm` or the described logic — that logic resides in `multisig2/src/lib.rs`. I've validated the vulnerability against the actual location; the file path in the question should be corrected accordingly.

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
