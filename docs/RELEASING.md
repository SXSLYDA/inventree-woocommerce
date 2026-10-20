# Releasing

The same release process as inventree-xero.

One version covers both halves:
- **InvenTree plugin:** `inventree_woocommerce/version.py` (`pyproject.toml` reads it from there).
- **WordPress plugin:** the `Version:` header and `INVENTREE_SHOP_VERSION` in `wordpress/inventree-shop/inventree-shop.php`, and `Stable tag:` in its `readme.txt`.

`build-and-bump.ps1` changes all of them together, and CI refuses a tag where they disagree.

**While you work:** add a line under `## Unreleased` in `CHANGELOG.md` for anything a user would notice. Say when:
- a release adds a migration;
- it changes the WordPress plugin, because the website then needs the new zip;
- it needs a newer inventree-xero, or the other way round.

## To release

In PyCharm's terminal, in the repo root:

```powershell
.\build-and-bump.ps1                 # patch: 0.6.0 -> 0.6.1
.\build-and-bump.ps1 -Bump minor     # new features: 0.6.0 -> 0.7.0
.\build-and-bump.ps1 -Version 1.0.0  # exact version
```

1. **Checks:** git is available; the tag doesn't exist yet locally or on GitHub; the new version is higher.
2. **Build:** regenerates `docs/SETTINGS.md` from the code.
3. **Tests:**
   - ruff and the unit tests;
   - the WordPress versions match `version.py`;
   - syntax checks of the panel and WordPress scripts if Node is installed, and of the PHP if PHP is installed.
   - Your venv needs `pip install -e . pytest ruff django djangorestframework` once.
4. **Bump:**
   - writes `version.py` and the WordPress plugin's version;
   - turns `## Unreleased` into `## X.Y.Z — date` and starts a fresh empty Unreleased section. It asks before releasing with no notes (`-Yes` skips the question);
   - builds `dist/inventree-shop-X.Y.Z.zip`.
5. **Commit and tag:** commits all changes as `Release vX.Y.Z` and adds an annotated tag `vX.Y.Z`. It does not push. To undo before pushing: `git tag -d vX.Y.Z; git reset --soft HEAD~1`.

It stops at the first problem, before changing anything. Then:

6. **Push the commit and the tag.**
   - PyCharm: *Git → Push* with **Push tags** ticked. It's off by default, and an unpushed tag can't be installed.
   - Terminal: `git push --follow-tags`.
7. **CI runs on the tag:**
   - unit tests on Python 3.11 to 3.13, lint, the migrations check, the settings doc and version checks;
   - the panel render test;
   - the live tests: real InvenTree 1.6.0 and 1.5.8 with the plugin, and on 1.6.0 a real WordPress with the inventree-shop plugin;
   - a check that the tag matches `version.py` and that `CHANGELOG.md` has the section.

   When everything passes it publishes a **GitHub release** with the changelog notes and `inventree-shop-X.Y.Z.zip`.
8. **Update the InvenTree server:** `sudo inventree-shop-update vX.Y.Z` (or just `sudo inventree-shop-update` for the newest), or let CI deploy it (below).
9. **Update the website** if the WordPress plugin changed: WordPress → Plugins → Add New → Upload Plugin → the zip from the release → *Replace current with uploaded*.

## Why tags and not "latest from main"

- The server's `plugins.txt` pins the plugin to a tag. A new tag is a new install link, so `invoke update` (which runs `pip install -U -r plugins.txt`) really installs it.
- Only releases you deliberately tagged and that passed CI can reach the server.
- The WordPress zip in the release is the one tested against that exact InvenTree plugin.

## Optional: deploy from GitHub

The `deploy` job in `ci.yml` updates the InvenTree server on release tags, but only after every test passes, and only when you switch it on. It uses the same secrets and environment as inventree-xero, and the two never deploy at the same time (shared `inventree-deploy` concurrency group).

1. Settings → Secrets and variables → Actions.
   - **Variable:** `SHOP_DEPLOY` = `true`.
   - **Secrets:**
     - `INVENTREE_HOST`, `INVENTREE_SSH_USER`, `INVENTREE_SSH_KEY` (a private key whose public half is in the server user's `~/.ssh/authorized_keys`);
     - `INVENTREE_SSH_KNOWN_HOSTS` (the output of `ssh-keyscan inventree.omgharness.com`).
2. Settings → Environments → **inventree-production** → *Required reviewers*: add yourself. Each deploy then waits for you to click **Approve**. Recommended.
3. The SSH user must be able to `sudo` without a password prompt, or be `root`.

The job sends `tools/server-update.sh` over SSH and runs it with the tag, so a GitHub deploy does exactly what a manual update does:
- backup;
- pin;
- update;
- restart;
- version check;
- plugin and migration check.

The WordPress plugin is not deployed automatically. Upload it on the website.

## Migrations

Add a new numbered migration for any model change, and never edit a released one. Mention it in the changelog, because it decides whether rolling back is safe.
