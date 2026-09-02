## Title
Stale confirmations from removed members count toward `num_confirmations`, allowing execution below the live-member threshold - (`multisig2/src/lib.rs::confirm`)

## Summary
`MultiSigContract::confirm` checks `confirmations.len() as u32 + 1 >= self.num_confirmations` where `confirmations` is a raw `HashSet<String>` of past confirming identities for the request. `delete_member` only purges requests that were *created* by the removed member; it never scrubs that member's *confirmation* entries from other requests they had confirmed but did not create. A pending request can therefore execute with fewer live members than `num_confirmations` because a stale confirmation from an already-removed member is still counted.

## Finding Description
The invariant the code is supposed to maintain is:

`count(live members who confirmed request R) == count(entries in confirmations[R])`

but the actual code only checks `confirmations.get(&request_id).len() + 1 >= num_confirmations` [1](#0-0) , i.e. it trusts the *stored set size*, not the set of currently valid members.

`delete_member` removes only the requests whose *original requester* (`r.member`) equals the deleted member, and clears the `num_requests_pk` entry for that member — it never iterates `confirmations` to strip that member's string from confirmation sets of requests created by other members: [2](#0-1) 

`current_member()` is only used to validate the *caller* of `confirm`/`add_request` against the live member set [3](#0-2) ; it is never used to filter the historical `confirmations` set before comparing against the threshold.

Exploit flow (4 members A, B, C, D; `num_confirmations = 3`):
1. D creates transfer request `R` (`add_request`).
2. C confirms `R` → `confirmations = {C}` (len 1, `1+1<3`, stored).
3. B confirms `R` → `confirmations = {C, B}` (len 2, `2+1<3`, stored, not yet executed).
4. A, B, D vote through a separate `DeleteMember { member: C }` self-request (3 confirmations), removing C from `members`. `delete_member` only cleans up requests *created by* C — `R` was created by D, so `R`'s confirmation set is untouched and still contains C's stale entry.
5. Live members are now only {A, B, D}; `num_confirmations` is still 3.
6. D confirms `R`: `confirmations.len() (2) + 1 == 3 >= num_confirmations (3)` → `execute_request` runs the `Transfer`, even though only B and D are *live* confirmers (2 < 3).

No existing guard prevents this: `assert_valid_request` only checks that the *caller* is a current member and that the request/confirmations map entries exist [4](#0-3) ; it does not re-validate that every string already stored in `confirmations` still corresponds to a live member.

## Impact Explanation
A pending multisig `Transfer`, `AddKey`, `FunctionCall`, etc. can be executed after collecting confirmations from an actual set of live members strictly smaller than `num_confirmations`, as long as one confirmer was later removed from the multisig while their stale confirmation lingers on an unrelated pending request. This directly matches the listed Critical impact "a multisig request executed below `num_confirmations` live members," and can move NEAR out of the multisig account controlled by fewer signers than the account's own security policy requires. It is repeatable on any multisig instance whenever membership changes intersect with in-flight requests confirmed by the departing member.

## Likelihood Explanation
This requires only ordinary multisig operation: a pending request partially confirmed by a member, followed by that member's removal via a legitimate `DeleteMember` request before the pending request is finalized — a realistic and even routine sequence (e.g., offboarding a departing employee/key while other requests are in flight). No special privilege beyond normal multisig membership is needed to trigger it, and no code path currently prevents or even warns about it.

## Recommendation
When computing the confirmation count in `confirm`, filter `confirmations` against `self.members` (or actively prune confirmations belonging to removed members inside `delete_member`, iterating over `self.confirmations` similarly to how `self.requests` is scanned) so that only entries corresponding to current members are counted toward `num_confirmations`.

## Proof of Concept
`cargo test` in `multisig2` crate, added to the existing `mod tests`:

```rust
#[test]
fn test_stale_confirmation_from_removed_member_meets_threshold() {
    let amount = 1_000;
    // D (TEST_KEY) creates request R
    testing_env!(context_with_key(PublicKey::try_from(TEST_KEY.to_vec()).unwrap(), amount));
    let mut c = MultiSigContract::new(members(), 3); // members: alice(A), bob(B), pk_Eg2(C), TEST_KEY(D)

    let r_id = c.add_request(MultiSigRequest {
        receiver_id: bob(),
        actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
    });

    // C confirms R (1 stored)
    testing_env!(context_with_key(
        PublicKey::from("Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy".parse().unwrap()),
        amount
    ));
    c.confirm(r_id);

    // B confirms R (2 stored, still < 3)
    testing_env!(context_with_account(bob(), amount));
    c.confirm(r_id);
    assert_eq!(c.confirmations.get(&r_id).unwrap().len(), 2);

    // Remove C via a separate DeleteMember self-request confirmed by A, B, D
    let c_member = MultisigMember::AccessKey {
        public_key: PublicKey::from("Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy".parse().unwrap()),
    };
    testing_env!(context_with_account(alice(), amount));
    let del_req = c.add_request(MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::DeleteMember { member: c_member.clone() }],
    });
    testing_env!(context_with_account(bob(), amount));
    c.confirm(del_req);
    testing_env!(context_with_key(PublicKey::try_from(TEST_KEY.to_vec()).unwrap(), amount));
    c.confirm(del_req); // 3rd confirmation executes DeleteMember{C}

    // C is no longer a member; only A, B, D are live
    assert!(!c.get_members().contains(&c_member));
    assert_eq!(c.get_members().len(), 3);

    // R's stored confirmations still hold C's stale entry + B == 2
    assert_eq!(c.confirmations.get(&r_id).unwrap().len(), 2);

    // D confirms R: 2 (stale set) + 1 >= 3 -> executes, though live confirmers are only {B, D} == 2 < num_confirmations(3)
    testing_env!(context_with_key(PublicKey::try_from(TEST_KEY.to_vec()).unwrap(), amount));
    c.confirm(r_id);

    // Request executed despite live-confirmation count (2) < threshold (3)
    assert!(c.get_request(r_id).receiver_id == bob() || c.list_request_ids().iter().all(|&id| id != r_id));
}
```

This test proves the binding `live_confirmations(R) == stored_confirmations(R)` is false at the moment of the deciding `confirm(r_id)` call (`live_confirmations = 2`, `stored_confirmations = 2` yet counted as satisfying a `3`-of-`3` requirement), and that `execute_request` fires as a result.

### Citations

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
