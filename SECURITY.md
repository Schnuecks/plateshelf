# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Report them privately through
GitHub instead: on the repository page open **Security → Report a vulnerability**. You will
get an answer within a few days, and a fix is released as soon as possible, independent of
the normal release plan.

If that option is not shown, contact the maintainer [@Schnuecks](https://github.com/Schnuecks)
through GitHub and ask for a private channel, without describing the problem publicly.

Helpful details: the PlateShelf version (shown in the footer), how PlateShelf is exposed
(home network only, reverse proxy, sign-in on or off) and the steps to reproduce.

## Supported versions

Security fixes go into the latest release. Please update to it before reporting
(`docker compose pull && docker compose up -d`).

## Scope

PlateShelf is meant for the home network. It has an optional sign-in and protection
against requests from other websites, but it is not designed to be exposed to the
internet without a reverse proxy with its own authentication.

Printer access codes, passwords and API keys are stored encrypted (AES-256-GCM) with a key
derived from `SECRET_KEY`, which is only read from the environment. Without `SECRET_KEY`
they are stored in plain text, and the settings page warns about it. The sign-in password
can be given as a scrypt hash (`AUTH_PASSWORD_HASH`).
