# `errors`

Every error the five tools raise derives from `DatheonError`, carries a stable code and knows which project raised it. The taxonomy is what lets the CLIs print one message and exit `1`, and what makes the [error catalogue](error_catalogue.md) generatable rather than hand-written.

::: errors
