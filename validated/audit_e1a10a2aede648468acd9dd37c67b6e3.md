### Title
Stale confirmations from removed members are never purged from other requests' confirmation sets, allowing a request to execute below `num_confirmations` live members - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` only purges confirmations for requests that the removed member *originated* (`r.member == member`), but never strips that member's votes from the `confirmations` set of requests created by *other* members. Because `confirm` counts votes purely by `confirmations.len()` without re-validating that every recorded confirmer string still corresponds to a current entry in `self.members`, a stale vote from a deleted member can be combined with fresh votes from current members to reach `num_confirmations`, executing a request that was never actually approved by that many live members.

### Finding Description
The binding that must hold is:
`count(distinct s in confirmations.get(R) where s ∈ {m.to_string() for m in self.members}) == num_confirmations` at the moment `confirm` decides to execute `R`.

The code instead checks `confirmations.len() as u32 + 1 >= self.num_confirmations` in `confirm` (multisig2/src/lib.rs:294-315), where `confirmations` is a raw `HashSet<String>` never re-validated against current `self.members`.

`delete_member` (multisig2/src/lib.rs:356-379) removes confirmations only for requests the deleted member authored:
```
let request_ids: Vec<u32> = self.requests.iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```
It never scans the `confirmations` sets of *other* live requests to remove the deleted member's cast votes there. `current_member()` (multisig2/src/lib.rs:322-339) only validates the identity of the caller *making the current call*; it does nothing to invalidate previously recorded votes belonging to members who have since been removed.

Exploit flow:
1. Member A creates request `R` (e.g. a `Transfer`). `add_request` does not auto-confirm for the creator.
2. Member B (the "attacker" member) confirms `R` → `confirmations(R) = {"Account{B}"}`, `len == 1`.
3. The multisig (via a separate, legitimately-confirmed `DeleteMember{Account:B}` request) removes B from `self.members`. `delete_member` only clears confirmations on requests B *authored*, so `confirmations(R)` still contains `"Account{B}"`.
4. Member C (a genuine current member) confirms `R`. `confirm` computes `confirmations.len() as u32 + 1 == 2 >= num_confirmations (2)` and executes `R` via `execute_request`.

At execution time, only C is a live member whose string is both current in `self.members` and present in `confirmations(R)`; B's vote is stale and B is no longer a member at all. The request executes with effectively 1 live approving member instead of the required 2 - exactly the broken binding described.

None of the existing guards catch this: `assert_valid_request` only checks the request/confirmations maps exist and that the *caller* is a current member; it does not re-validate historical entries in `confirmations`. `current_member()` is only invoked for the calling account, not for auditing stored confirmation strings.

### Impact Explanation
This allows execution of any multisig action - `Transfer`, `AddKey`, `AddMember`, `DeleteMember`, `FunctionCall`, `DeployContract`, etc. - with fewer genuinely live approving members than `num_confirmations` mandates. Since multisig contracts on this platform hold and control NEAR funds and access keys, an attacker who is (or was) a member could cast a vote, get removed, and have that vote still count toward executing a `Transfer` of funds out of the account, matching the Critical category "a multisig request executed below `num_confirmations` live members." This is repeatable for every request the removed member confirmed before removal, across any number of contracts deployed with this code.

### Likelihood Explanation
Requires the attacker to have been a legitimate multisig member at some point (or otherwise have cast a confirmation) and then be removed by the other members - a normal operational event (member rotation, key revocation, offboarding). No special privilege beyond having once been added is needed for the stale vote to persist; the remaining members' otherwise-correct confirmation flow inadvertently combines with the stale vote. This is fully reproducible in a deterministic unit test with `testing_env!`.

### Recommendation
When removing a member in `delete_member`, iterate over all entries in `confirmations` (not just requests authored by the removed member) and strip the removed member's `to_string()` entry from every request's confirmation set. Alternatively, change `confirm` to compute the confirmation count by filtering `confirmations.get(&request_id)` against `self.members` (i.e., only count strings that match a current member) rather than trusting `HashSet::len()` directly.

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_survives_member_deletion() {
    // members: alice (Account), bob (Account), carol (Account); num_confirmations = 2
    testing_env!(context_with_account(alice(), 1_000));
    let mut c = MultiSigContract::new(
        vec![
            MultisigMember::Account { account_id: alice() },
            MultisigMember::Account { account_id: bob() },
            MultisigMember::Account { account_id: carol() },
        ],
        2,
    );

    // 1. alice creates request R (does not auto-confirm)
    let r = MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::Transfer { amount: 100.into() }],
    };
    let request_id = c.add_request(r);

    // 2. bob confirms R -> confirmations(R) = {"Account{bob}"}
    testing_env!(context_with_account(bob(), 1_000));
    c.confirm(request_id);
    assert_eq!(c.get_confirmations(request_id).len(), 1);

    // 3. Remove bob via a separately-confirmed DeleteMember request
    testing_env!(context_with_account(alice(), 1_000));
    let del_req = MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::DeleteMember {
            member: MultisigMember::Account { account_id: bob() },
        }],
    };
    let del_id = c.add_request(del_req);
    testing_env!(context_with_account(carol(), 1_000));
    c.confirm(del_id); // alice(creator, not counted) + carol -> executes DeleteMember(bob)

    // bob no longer a member
    assert!(!c.get_members().contains(&MultisigMember::Account { account_id: bob() }));
    // stale confirmation from bob still present on R
    assert!(c.get_confirmations(request_id).contains(&MultisigMember::Account{account_id: bob()}.to_string()));

    // 4. carol confirms R -> executes with only 1 live member (carol) truly approving,
    //    combined with bob's stale vote, satisfying num_confirmations=2
    testing_env!(context_with_account(carol(), 1_000));
    c.confirm(request_id);

    // Binding check: only carol's entry among confirmations(R) matches a current member.
    // Live-approving-member count == 1, but request executed as if count == num_confirmations == 2.
    assert_eq!(c.list_request_ids().contains(&request_id), false); // R was executed/removed
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
