# AV-98

AV-98 is an experimental client for the
[Gemini protocol](https://gemini.circumlunar.space).  It is derived from the
[gopher client VF-1](https://github.com/solderpunk/VF-1) by the same author.
AV-98 is "experimental" in the sense that it may occasionally extend or deviate
from the official Gemini specification for the purposes of, well,
experimentation.  Despite this, it is expected to be stable enough for regular
daily use at the same time.

## Dependencies

AV-98 has no "strict dependencies", i.e. it will run and work without anything
else beyond the Python standard library.  However, it will "opportunistically
import" a few other libraries if they are available to offer an improved
experience.

* The [ansiwrap library](https://pypi.org/project/ansiwrap/) may result in
  neater display of text which makes use of ANSI escape codes to control colour.
* The [cryptography library](https://pypi.org/project/cryptography/) will
  provide a better and slightly more secure experience when using the default
  TOFU certificate validation mode and is highly recommended.

## Features

* TOFU or CA server certificate validation
* Extensive client certificate support if an `openssl` binary is available
* Ability to specify external handler programs for different MIME types
* Gopher proxy support (e.g. for use with
  [Agena](https://tildegit.org/solderpunk/agena))
* Advanced navigation tools like `tour` and `mark` (as per VF-1)
* Bookmarks
* IPv6 support
* Supports any character encoding recognised by Python

## Lightning introduction

You use the `go` command to visit a URL, e.g. `go gemini.circumlunar.space`.

Links in Gemini documents are assigned numerical indices.  Just type an index to
follow that link.

If a Gemini document is too long to fit on your screen, use the `less` command
to pipe it to the `less` pager.

Use the `help` command to learn about additional commands.
