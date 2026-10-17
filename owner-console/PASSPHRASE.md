# The owner console passphrase

The console at https://pivot-owner-847d37.azurewebsites.net has one passphrase. This note covers what it is, where it lives, and how to change it.

## Is it always the same?

**Yes, until you change it.** It is random, not derived from anything, so nobody (including us) can regenerate the same one. Running the generator again makes a completely different passphrase. The old one keeps working until the new hash is uploaded and the app reloads it.

## How it was made

`owner-console/tools/make_password.py` makes 12 groups of 6 random letters and digits (about 428 bits) using Python's cryptographic random generator. It writes two files:

| File | What it holds | Where it goes |
|---|---|---|
| `~/.config/pivot-ops/password` | the passphrase itself (mode 600, only your Mac account can read it) | **nowhere else**: this is the only copy |
| `~/.config/pivot-ops/password.hash` | an scrypt hash of it | Key Vault `pivot-ops-kv-847d37`, secret `ops-password-hash` |

The console compares what you type against the hash. Someone who could read the Azure settings would see only the hash, never the passphrase.

## Using it

```sh
pbcopy < ~/.config/pivot-ops/password    # copies it without printing it
```

Paste it on the console's sign-in page; don't type it, because five wrong attempts lock your address for 15 minutes. Afterwards, clear the clipboard with `pbcopy < /dev/null`.

## Back it up

If the laptop file is lost, the passphrase is gone for good. A password manager is a good second home for it, as long as you paste it in yourself. If it is lost anyway, make a new one (below). You don't need the old passphrase for that, only access to the Azure account.

## Changing it

Do this if the passphrase leaks, the laptop is lost, someone else saw it, or you just want a new one.

```sh
cd ~/Downloads/Second_Star-owner/owner-console
source ~/.config/pivot-ops/az.sh        # azx = az with the Homebrew expat fix

# 1. A new passphrase (overwrites the old file) and its hash
python3 tools/make_password.py ~/.config/pivot-ops/password > ~/.config/pivot-ops/password.hash
tr -d '\n' < ~/.config/pivot-ops/password.hash > ~/.config/pivot-ops/password.hash.raw

# 2. Upload the hash to Key Vault (read from the file, never on the command line)
azx keyvault secret set --vault-name pivot-ops-kv-847d37 -n ops-password-hash \
  --file ~/.config/pivot-ops/password.hash.raw --encoding utf-8 -o none

# 3. Make the app read it now (otherwise Azure can take up to 24 hours), then restart
SUB=$(azx account show --query id -o tsv)
azx rest --method post --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/pivot-ops/providers/Microsoft.Web/sites/pivot-owner-847d37/config/configreferences/appsettings/refresh?api-version=2022-03-01"
azx webapp restart -n pivot-owner-847d37 -g pivot-ops
```

Then sign in with the new passphrase (`pbcopy < ~/.config/pivot-ops/password`). The old one stops working once the app has restarted. The restart also ends every open console session, including any session someone else may have.

## Notes

- Signing in from a terminal (`owner-console/pivot_codes.py`) reads the same file, so it picks up a new passphrase automatically.
- The passphrase is separate from the key the console uses to talk to the Pivot server (`~/.config/pivot-ops/ops-key`). Changing one never changes the other.
- If `az` crashes with a `pyexpat` / `libexpat` error, use `azx` as above (`~/.config/pivot-ops/az.sh`).
