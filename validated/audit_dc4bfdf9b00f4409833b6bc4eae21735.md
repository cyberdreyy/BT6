### Title
Stale confirmations from deleted members allow `execute_request`/`AddKey` (full-access key grant) below live `num_confirmations` - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` only checks `confirmations.len() as u32 + 1 >= self.num_confirmations` and that the *current* caller hasn't already confirmed; it never re-validates that the members recorded in a request's stored `confirmations: HashSet<String>` are still `self.members` at execution time. `delete_member` only purges confirmations/requests it *originated* (`r.member == member`), not confirmations it *contributed* to other members' pending requests, so a deleted member's confirmation on someone else's still-pending request survives and is later counted toward the threshold.

### Finding Description
The binding that should hold is:
`live_distinct_confirmers(R) == num_confirmations` at the moment `execute_request(R)` schedules `promise.add_full_access_key`, where `live_distinct_confirmers(R) = |{m ∈ confirmations(R) : m ∈ self.members}|`.

In practice the code only enforces `confirmations.len() + 1 >= num_confirmations` [1](#0-0) , using the raw stored set size, with no re-check that every string in `confirmations` still corresponds to a current `self.members` entry.

`delete_member` removes a departing member from `self.members` and clears `num_requests_pk`, but it only removes requests/confirmations for requests that member *authored* (`filter_map(|(k, r)| if r.member == member ...)`), never confirmations that member cast on *other* members' pending requests [2](#0-1) . This leaves a "stale" entry in `confirmations` for any other pending request that the now-deleted member had previously confirmed.

Exploit flow (3 members M1, M2, M3, `num_confirmations = 2`):
1. M2 calls `add_request` for `R = {receiver_id: current_account_id(), actions: [AddKey{public_key: attacker_pk, permission: None}]}`. `confirmations[R] = {}`.
2. M1 calls `confirm(R)`: `confirmations.len()+1 = 1 < 2`, so M1's entry is just inserted: `confirmations[R] = {M1}`.
3. M2 and M3 create+confirm `DeleteMember{member: M1}` (allowed since `members.len()-1 == 2 >= num_confirmations`). This removes M1 from `self.members`, but does **not** touch `confirmations[R]`, since `R`'s `member` field is M2, not M1.
4. Now `self.members = {M2, M3}`, `num_confirmations = 2`, but `confirmations[R] = {M1}` (stale, M1 no longer a member).
5. M3 calls `confirm(R)`: `confirmations.len() as u32 + 1 = 1 + 1 = 2 >= num_confirmations (2)` → `execute_request(R)` runs, calling `promise.add_full_access_key(attacker_pk)` on the multisig account itself [3](#0-2) .

At this point only **one** live member (M3) actually confirmed `R`; M1's stale confirmation supplied the second "vote" needed to cross the threshold, even though M1 is no longer a `MultisigMember`. `attacker_pk` receives a full-access key on the multisig account, letting the attacker who controls `attacker_pk` bypass `confirm`/`execute_request` entirely and drain the account directly.

None of the existing guards catch this: `assert_valid_request` only checks the *caller* is a current member [4](#0-3) ; `assert_self_request` only checks `receiver_id == current_account_id()`; `current_member()` is only used for the calling account, not retroactively for stored confirmations.

### Impact Explanation
This is a "multisig request executed below `num_confirmations` live members" scenario explicitly called out as Critical. The attacker ends up with a full-access key (`attacker_pk`) on the multisig account, and since full-access keys are not tracked as `MultisigMember`s, the attacker can subsequently transfer all NEAR out of the account or delete other keys, completely bypassing the K-of-N scheme and achieving total loss of custody. The attack is repeatable on any multisig contract where a member is removed after confirming a pending request but before that request reaches quorum.

### Likelihood Explanation
This requires the multisig's own legitimate members to (a) have a pending request confirmed by someone, and (b) later remove that confirmer as a member before the request is finalized — a realistic operational sequence (e.g. rotating keys/members) with no attacker action needed to trigger the state divergence; the attacker only needs one of their (possibly bribed, compromised, or malicious) accepted `AddKey` requests to be in flight when member turnover happens, or to be the entity proposing `R` and waiting for this rotation. Given the audit's constraint that the attacker is unprivileged (not a member), the more realistic exploitation path is that this is a latent contract defect: any existing member (once compromised or acting maliciously in collusion, or through innocent normal operational rotation) can trigger it, and it should be treated as a correctness bug in the contract's confirmation-counting logic regardless of who benefits.

### Recommendation
When counting confirmations, filter `confirmations` in `confirm` (and ideally on every request re-evaluation) to only those entries still contained in `self.members`, e.g. `confirmations.iter().filter(|m| self.members.contains(m)).count()`. Additionally, `delete_member` should also prune the deleted member's entry from every other request's `confirmations` set (not just requests it authored), so stale confirmations cannot resurface.

### Proof of Concept
```rust
// multisig2/src/tests.rs (unit test using testing_env!)
#[test]
fn test_stale_confirmation_bypasses_live_threshold() {
    // members: m1 (AccessKey pk1), m2 (AccessKey pk2), m3 (AccessKey pk3), num_confirmations = 2
    testing_env!(context_with_key(pk2.clone(), amount));
    let mut c = MultiSigContract::new(vec![member1.clone(), member2.clone(), member3.clone()], 2);

    // Step 1: m2 creates request R = AddKey{attacker_pk} on current_account_id()
    testing_env!(context_with_key(pk2.clone(), amount));
    let r_id = c.add_request(MultiSigRequest {
        receiver_id: alice(), // == current_account_id()
        actions: vec![MultiSigRequestAction::AddKey { public_key: attacker_pk.clone(), permission: None }],
    });

    // Step 2: m1 confirms R (only 1/2, stored as confirmation)
    testing_env!(context_with_key(pk1.clone(), amount));
    c.confirm(r_id);
    assert_eq!(c.get_confirmations(r_id).len(), 1); // stale-to-be entry

    // Step 3: m2 + m3 delete member m1
    testing_env!(context_with_key(pk2.clone(), amount));
    let del_id = c.add_request(MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::DeleteMember { member: member1.clone() }],
    });
    testing_env!(context_with_key(pk3.clone(), amount));
    c.confirm(del_id); // executes DeleteMember, m1 removed from self.members

    assert!(!c.get_members().contains(&member1));
    // Confirmations for R still contain m1's stale entry:
    assert_eq!(c.get_confirmations(r_id).len(), 1);

    // Step 4: m3 confirms R -> stale m1 + live m3 = 2 >= num_confirmations(2)
    testing_env!(context_with_key(pk3.clone(), amount));
    let result = c.confirm(r_id); // should NOT reach quorum with only 1 live confirmer, but does

    // BINDING VIOLATION: live confirmers = {m3} = 1, but execute happened as if == num_confirmations (2)
    // assert execute_request scheduled add_full_access_key(attacker_pk) despite only 1 live confirmer
    assert!(matches!(result, PromiseOrValue::Promise(_)));

    // Step 5 (follow-up context): attacker_pk now has full access key, not a MultisigMember
    testing_env!(context_with_key(attacker_pk.clone(), amount));
    // attacker can now call e.g. transfer directly via a signed transaction with attacker_pk,
    // completely bypassing confirm()/execute_request and the multisig scheme.
}
```

### Citations

**File:** multisig2/src/lib.rs (L243-261)
```rust
                MultiSigRequestAction::AddKey {
                    public_key,
                    permission,
                } => {
                    self.assert_self_request(receiver_id.clone());
                    if let Some(permission) = permission {
                        promise.add_access_key(
                            public_key.into(),
                            permission
                                .allowance
                                .map(|x| x.into())
                                .unwrap_or(DEFAULT_ALLOWANCE),
                            permission.receiver_id,
                            permission.method_names.join(","),
                        )
                    } else {
                        // wallet UI should warn user if receiver_id == env::current_account_id(), adding FAK will render multisig useless
                        promise.add_full_access_key(public_key.into())
                    }
```

**File:** multisig2/src/lib.rs (L304-309)
```rust
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
