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

## RC files

You can use an RC file to automatically run any sequence of valid AV-98
commands upon start up.  This can be used to make settings controlled with the
`set` or `handler` commanders persistent.  You can also put a `go` command in
your RC file to visit a "homepage" automatically on startup, or to pre-prepare
a `tour` of your favourite Gemini sites.

The RC file should be called `av98rc`.  AV-98 will look for it first in
`~/.av98/` and second in `~/.config/av98/`.  Note that either directory might
already exist even if you haven't created it manually, as AV-98 will, if
necessary, create the directory itself the first time you save a bookmark (the
bookmark file is saved in the same location).  AV-98 will create
`~/.config/av98` only if `~/.config/` already exists on your system, otherwise
it will create `~/.av98/`.
