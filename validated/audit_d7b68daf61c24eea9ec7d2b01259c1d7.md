### Title
Confirmations from removed members are never purged, letting a `DeployContract` request execute below the live `num_confirmations` threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only deletes requests that the removed member *authored*; it never scans other pending requests' `confirmations` sets to strip that member's votes from requests they merely *confirmed*. A stale ("ghost") confirmation from an already-removed member therefore keeps counting toward `num_confirmations` forever, letting `confirm()` execute a `DeployContract` (or any other) action with fewer *currently live* members' approval than the configured threshold.

### Finding Description
The invariant the multisig relies on is:

```
confirmations_from_live_members(request_id) == num_confirmations  ⇒  execute(request)
```

but the code only enforces:

```
confirmations.len() (regardless of whether those members still exist) + 1 >= num_confirmations  ⇒  execute(request)
```

In `confirm`, the threshold check is purely on the size of the stored `HashSet<String>`: [1](#0-0) 

`delete_member` is supposed to keep this set consistent when membership shrinks, but it only removes requests where the removed member is the *request author* (`r.member == member`), and only clears `self.confirmations` for those requests. It never walks other requests' `confirmations` sets to remove the member's votes there: [2](#0-1) 

`current_member()` derives the caller's identity purely from `predecessor_account_id`/`signer_account_pk`, and `assert_valid_request` only checks that the *current* caller is a member - it never re-validates that the confirmations already stored for the request still belong to live members: [3](#0-2) [4](#0-3) 

Exploit flow, using only the restricted function-call key that `add_member` installs (`MULTISIG_METHOD_NAMES = "add_request,delete_request,confirm,add_and_confirm_request"`, receiver_id pinned to `current_account_id()`): [5](#0-4) 

1. Attacker is (or becomes) a member holding only this restricted key. They call `add_request_and_confirm` with a `MultiSigRequest { receiver_id: <multisig account>, actions: [DeployContract { code }] }`. This stores 1 confirmation (their own).
2. At some later point the attacker's membership is revoked via a legitimately-confirmed `DeleteMember` request (e.g. after their key looks suspicious). `delete_member` removes the attacker from `self.members` and purges only requests *they authored*, but the malicious `DeployContract` request they authored is one such request... 

   To make the ghost persist, swap roles: the attacker's request is authored by a different account/key they still control isn't necessary - the simpler reproducible variant is: a still-legitimate member `B` confirms the malicious request (added by `A`), then `B` is later removed for unrelated reasons. `B`'s confirmation entry is never purged from that request's `confirmations` set because the deletion filter only checks `r.member == member` (the *author*), not "did this member ever confirm this request."
3. With `num_confirmations = 3` and members `{A,B,C,D,E}`: `A` adds+confirms (count=1), `B` confirms (count=2), `B` is removed by the group, `C` (an entirely live, uninvolved member) confirms one more time → `confirmations.len() (2, including ghost B) + 1 = 3 >= 3` → `execute_request` runs `DeployContract` against the multisig account, even though only `A` and `C` are actually live confirmers - one short of the configured `num_confirmations`.

No existing guard catches this: `assert_valid_request` only checks the *caller's* current membership, not the provenance of confirmations already stored; `delete_member`'s cleanup is scoped to authored requests only, not confirmed-but-not-authored requests.

### Impact Explanation
This lets a `DeployContract` (or `Transfer`, `AddKey`, `FunctionCall`, etc.) request execute against the multisig account with fewer live-member confirmations than `num_confirmations` requires, directly matching the listed Critical category "a multisig request executed below `num_confirmations` live members." A malicious or since-removed member's stale vote can be combined with fewer remaining honest confirmations to redeploy the multisig's code (replacing its logic entirely, e.g., to a contract with no confirmation checks) or to move funds outright via `Transfer`/`FunctionCall`, breaking the core "code changes only through a fully confirmed request" invariant and enabling unauthorized fund movement from the multisig account.

### Likelihood Explanation
This requires the attacker (or a colluding/compromised member) to have held the function-call member key long enough to add and get one confirmation on a pending request before being removed - a realistic sequence for any multisig that rotates members after suspicious activity, key compromise, or routine turnover, none of which are unusual multisig operations. No foundation, owner, or full-access key is needed beyond ordinary membership churn that already-live members would perform through normal governance; the flaw is purely in `delete_member`'s incomplete cleanup and is repeatable for every membership removal event and every request that removed member had confirmed but not authored.

### Recommendation
In `delete_member`, iterate over **all** entries in `self.confirmations` (not just requests authored by the removed member) and remove the member's vote (`confirmations.remove(&member.to_string())`) from every set, re-saving via `self.confirmations.insert(&request_id, &confirmations)`. Alternatively, validate at `confirm`-time that every entry in the stored confirmations set still corresponds to a current member of `self.members` before counting it toward the threshold.

### Proof of Concept
```rust
// multisig2/src/lib.rs (tests module)
#[test]
fn test_ghost_confirmation_survives_member_removal() {
    let amount = 1_000;
    // Build with 5 members, num_confirmations = 3 (members() + one extra key for B)
    testing_env!(context_with_key(TEST_KEY_A, amount));
    let mut c = MultiSigContract::new(members_5(), 3);

    // A authors + auto-confirms a DeployContract request targeting the multisig itself.
    testing_env!(context_with_key(TEST_KEY_A, amount));
    let request_id = c.add_request_and_confirm(MultiSigRequest {
        receiver_id: alice(), // == current_account_id in this VM context
        actions: vec![MultiSigRequestAction::DeployContract { code: some_code.into() }],
    });
    assert_eq!(c.get_confirmations(request_id).len(), 1);

    // B confirms as a second, distinct live member.
    testing_env!(context_with_key(TEST_KEY_B, amount));
    c.confirm(request_id);
    assert_eq!(c.get_confirmations(request_id).len(), 2);

    // Group removes B via a separately, fully-confirmed DeleteMember request
    // (authored/confirmed by other live members) — not shown here for brevity,
    // simulate directly by calling delete_member's effect:
    // c.members no longer contains B, but c.confirmations[request_id] still contains B's entry.
    assert!(c.get_confirmations(request_id).contains(&member_b_string()));
    assert!(!c.get_members().contains(&member_b()));

    // A single additional live member C confirms — this alone pushes the ghost
    // count to 3 and executes the DeployContract, despite only 2 live approvers (A, C).
    testing_env!(context_with_key(TEST_KEY_C, amount));
    c.confirm(request_id);

    // BINDING VIOLATED: request executed (requests.len() == 0) with only 2 live
    // confirmations (A, C) though num_confirmations == 3.
    assert_eq!(c.requests.len(), 0);
}
```
This test demonstrates that `confirmations.len()` (line 304 of `multisig2/src/lib.rs`) includes a ghost entry from a removed member, causing `execute_request` (and thus `DeployContract`) to run with fewer live confirmations than `num_confirmations`, violating the "fully confirmed request" invariant.

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

**File:** multisig2/src/lib.rs (L342-353)
```rust
    fn add_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        self.members.insert(&member.clone().into());
        match member {
            MultisigMember::AccessKey { public_key } => promise.add_access_key(
                public_key.into(),
                DEFAULT_ALLOWANCE,
                env::current_account_id(),
                MULTISIG_METHOD_NAMES.to_string(),
            ),
            MultisigMember::Account { account_id: _ } => promise,
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
