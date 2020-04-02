#!/usr/bin/env python3
# AV-98 Gemini client
# (C) 2019 Solderpunk <solderpunk@sdf.org>
# Dervied from VF-1 (https://github.com/solderpunk/VF-1),
# which features contributions from:
#  - Alex Schroeder <alex@gnu.org>
#  - Joseph Lyman <tfurrows@sdf.org>
#  - Adam Mayer (https://github.com/phooky)
#  - Paco Estaban <paco@onna.be>

import argparse
import cmd
import cgi
import codecs
import collections
import fnmatch
import io
import mimetypes
import os.path
import random
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import urllib.parse
import ssl
import sys
import time
import webbrowser

_MAX_REDIRECTS = 5

# Command abbreviations
_ABBREVS = {
    "a":    "add",
    "b":    "back",
    "bb":   "blackbox",
    "bm":   "bookmarks",
    "book": "bookmarks",
    "f":    "fold",
    "fo":   "forward",
    "g":    "go",
    "h":    "history",
    "hist": "history",
    "l":    "less",
    "n":    "next",
    "p":    "previous",
    "prev": "previous",
    "q":    "quit",
    "r":    "reload",
    "s":    "save",
    "se":   "search",
    "/":    "search",
    "t":    "tour",
    "u":    "up",
}

_MIME_HANDLERS = {
    "application/pdf":      "xpdf %s",
    "audio/mpeg":           "mpg123 %s",
    "audio/ogg":            "ogg123 %s",
    "image/*":              "feh %s",
    "text/html":            "lynx -dump -force_html %s",
    "text/plain":           "cat %s",
    "text/gemini":          "cat %s",
}

protocol = ssl.PROTOCOL_TLSv1_2 if sys.version_info.minor < 5 else ssl.PROTOCOL_TLS
context = ssl.SSLContext(protocol)
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
# Impose minimum TLS version
if sys.version_info.minor >= 7:
    context.minimum_version = ssl.TLSVersion.TLSv1_2
else:
    context.options |= ssl.OP_NO_TLSv1_1
    context.options |= ssl.OP_NO_SSLv3
    context.options |= ssl.OP_NO_SSLv2
context.set_ciphers("AES+DHE:AES+ECDHE:CHACHA20+DHE:CHACHA20+ECDHE:!SHA1:@STRENGTH")
# print(context.get_ciphers())


def fix_ipv6_url(url):
    if not url.count(":") > 2: # Best way to detect them?
        return url
    # If there's a pair of []s in there, it's probably fine as is.
    if "[" in url and "]" in url:
        return url
    # Easiest case is a raw address, no schema, no path.
    # Just wrap it in square brackets and whack a slash on the end
    if "/" not in url:
        return "[" + url + "]/"
    # Now the trickier cases...
    if "://" in url:
        schema, schemaless = url.split("://")
    else:
        schema, schemaless = None, url
    if "/" in schemaless:
        netloc, rest = schemaless.split("/",1)
        schemaless = "[" + netloc + "]" + "/" + rest
    if schema:
        return schema + "://" + schemaless
    return schemaless

standard_ports = {
        "gemini": 1965,
        "gopher": 70,
        "http": 80,
        "https": 443,
}

class GeminiItem():

    def __init__(self, url, name=""):
        if "://" not in url:
            url = "gemini://" + url
        self.url = fix_ipv6_url(url)
        self.name = name
        parsed = urllib.parse.urlparse(self.url)
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or standard_ports[self.scheme]
        self.path = parsed.path

    def root(self):
        return GeminiItem(self._derive_url("/"))

    def up(self):
        pathbits = list(os.path.split(self.path.rstrip('/')))
        # Don't try to go higher than root
        if len(pathbits) == 1:
            return self
        # Get rid of bottom component
        pathbits.pop()
        new_path = os.path.join(*pathbits)
        return GeminiItem(self._derive_url(new_path))

    def query(self, query):
        return GeminiItem(self._derive_url(query=query))

    def _derive_url(self, path="", query=""):
        """
        A thin wrapper around urlunparse which avoids inserting standard ports
        into URLs just to keep things clean.
        """
        return urllib.parse.urlunparse((self.scheme,
            self.host if self.port == standard_ports[self.scheme] else self.host + ":" + str(self.port),
            path or self.path, "", query, ""))

    def absolutise_url(self, relative_url):
        """
        Convert a relative URL to an absolute URL by using the URL of this
        GeminiItem as a base.
        """
        # Absolutise URL, which annoyingly needs a valid scheme...
        if self.url.startswith("gemini://"):
            base_url = self.url.replace("gemini://", "https://")
            was_gemini = True
        else:
            base_url = self.url
            was_gemini = False
        absolute = urllib.parse.urljoin(base_url, relative_url)
        if absolute != relative_url and was_gemini:
            absolute = absolute.replace("https://", "gemini://")
        return absolute

    def to_map_line(self, name=None):
        if name or self.name:
            return "=> {} {}\n".format(self.url, name or self.name)
        else:
            return "=> {}\n".format(self.url)

    @classmethod
    def from_map_line(cls, line, origin_gi):
        assert line.startswith("=>")
        assert line[2:].strip()
        bits = line[2:].strip().split(maxsplit=1)
        bits[0] = origin_gi.absolutise_url(bits[0])
        return cls(*bits)

CRLF = '\r\n'

# Cheap and cheerful URL detector
def looks_like_url(word):
    return "." in word and word.startswith("gemini://")

# GeminiClient Decorators
def needs_gi(inner):
    def outer(self, *args, **kwargs):
        if not self.gi:
            print("You need to 'go' somewhere, first")
            return None
        else:
            return inner(self, *args, **kwargs)
    outer.__doc__ = inner.__doc__
    return outer

class GeminiClient(cmd.Cmd):

    def __init__(self):
        cmd.Cmd.__init__(self)
        self.prompt = "\x1b[38;5;202m" + "AV-98" + "\x1b[38;5;255m" + "> " + "\x1b[0m"
        self.gi = None
        self.history = []
        self.hist_index = 0
        self.idx_filename = ""
        self.index = []
        self.index_index = -1
        self.lookup = self.index
        self.marks = {}
        self.page_index = 0
        self.permanent_redirects = {}
        self.previous_redirectors = set()
        self.tmp_filename = ""
        self.visited_hosts = set()
        self.waypoints = []

        self.options = {
            "debug" : False,
            "ipv6" : True,
            "timeout" : 10,
            "gopher_proxy" : "localhost:1965",
            "width" : 80,
            "auto_follow_redirects" : True,
        }

        self.log = {
            "start_time": time.time(),
            "requests": 0,
            "ipv4_requests": 0,
            "ipv6_requests": 0,
            "bytes_recvd": 0,
            "ipv4_bytes_recvd": 0,
            "ipv6_bytes_recvd": 0,
            "dns_failures": 0,
            "refused_connections": 0,
            "reset_connections": 0,
            "timeouts": 0,
        }

    def _go_to_gi(self, gi, update_hist=True, handle=True):
        """This method might be considered "the heart of AV-98".
        Everything involved in fetching a gemini resource happens here:
        sending the request over the network, parsing the response if
        its a menu, storing the response in a temporary file, choosing
        and calling a handler program, and updating the history."""
        # Don't try to speak to servers running other protocols
        if gi.scheme in ("http", "https"):
            webbrowser.open_new_tab(gi.url)
            return
        elif gi.scheme not in ("gemini", "gopher"):
            print("Sorry, no support for " + gi.scheme)
            return
        # Obey permanent redirects
        if gi.url in self.permanent_redirects:
            new_gi = GeminiItem(self.permanent_redirects[gi.url], name=gi.name)
            self._go_to_gi(new_gi)
            return
        # Do everything which touches the network in one block,
        # so we only need to catch exceptions once
        try:
            # Is this a local file?
            if not gi.host:
                address, f = None, open(gi.path, "rb")
            else:
                address, f = self._send_request(gi)
            # Read response header
            header = f.readline()
            header = header.decode("UTF-8").strip()
            self._debug("Response header: %s." % header)

        # Catch network errors which may happen on initial connection
        except Exception as err:
            # Print an error message
            if isinstance(err, socket.gaierror):
                self.log["dns_failures"] += 1
                print("ERROR: DNS error!")
            elif isinstance(err, ConnectionRefusedError):
                self.log["refused_connections"] += 1
                print("ERROR: Connection refused!")
            elif isinstance(err, ConnectionResetError):
                self.log["reset_connections"] += 1
                print("ERROR: Connection reset!")
            elif isinstance(err, (TimeoutError, socket.timeout)):
                self.log["timeouts"] += 1
                print("""ERROR: Connection timed out!
Slow internet connection?  Use 'set timeout' to be more patient.""")
            else:
                print("ERROR: " + str(err))
            return

        # Validate header
        status, meta = header.split(maxsplit=1)
        if len(header) > 1024 or len(status) != 2 or not status.isnumeric():
            print("ERROR: Received invalid header from server!")
            f.close()
            return

        # Update redirect loop/maze escaping state
        if not status.startswith("3"):
            self.previous_redirectors = set()

        # Handle non-SUCCESS headers, which don't have a response body
        # Inputs
        if status.startswith("1"):
            user_input = input(meta)
            self._go_to_gi(gi.query(user_input))
            return
        # Redirects
        elif status.startswith("3"):
            new_gi = GeminiItem(gi.absolutise_url(meta))
            if new_gi.url in self.previous_redirectors:
                print("Error: caught in redirect loop!")
            elif len(self.previous_redirectors) == _MAX_REDIRECTS:
                print("Error: refusing to follow more than %d consecutive redirects!" % _MAX_REDIRECTS)
            elif not self.options["auto_follow_redirects"]:
                follow = input("Follow redirect to %s? (y/n) " % new_gi.url)
                if follow.strip().lower() not in ("y", "yes"):
                    return
            else:
                self._debug("Following redirect to %s." % new_gi.url)
                self._debug("This is consecutive redirect number %d." % len(self.previous_redirectors))
                self.previous_redirectors.add(gi.url)
                if status == "31":
                    # Permanent redirect
                    self.permanent_redirects[gi.url] = new_gi.url
                self._go_to_gi(new_gi)
            return
        # Errors
        elif status.startswith("4") or status.startswith("5"):
            print("Error: %s" % meta)
            return
        # Client cert
        elif status.startswith("6"):
            print("Client certificates not supported.")
            return
        # Invalid status
        elif not status.startswith("2"):
            print("ERROR: Server returned undefined status code %s!" % status)
            return

        # If we're here, this must be a success and there's a response body
        assert status.startswith("2")
        mime = meta
        if mime == "":
            mime = "text/gemini; charset=utf-8"
        mime, mime_options = cgi.parse_header(mime)
        if "charset" in mime_options:
            try:
                codecs.lookup(mime_options["charset"])
            except LookupError:
                print("Header declared unknown encoding %s" % value)
                return

        # Read the response body over the network
        body = f.read()

        # Save the result in a temporary file
        ## Delete old file
        if self.tmp_filename:
            os.unlink(self.tmp_filename)
        ## Set file mode
        if mime.startswith("text/"):
            mode = "w"
            encoding = mime_options.get("charset", "UTF-8")
            try:
                body = body.decode(encoding)
            except UnicodeError:
                print("Could not decode response body using %s encoding declared in header!" % encoding)
                return
        else:
            mode = "wb"
            encoding = None
        ## Write
        tmpf = tempfile.NamedTemporaryFile(mode, encoding=encoding, delete=False)
        size = tmpf.write(body)
        tmpf.close()
        self.tmp_filename = tmpf.name
        self._debug("Wrote %d byte response to %s." % (size, self.tmp_filename))

        # Pass file to handler, unless we were asked not to
        if handle:
            if mime == "text/gemini":
                self._handle_index(body, gi)
            else:
                cmd_str = self._get_handler_cmd(mime)
                try:
                    subprocess.call(shlex.split(cmd_str % tmpf.name))
                except FileNotFoundError:
                    print("Handler program %s not found!" % shlex.split(cmd_str)[0])
                    print("You can use the ! command to specify another handler program or pipeline.")

        # Update state
        self.gi = gi
        self.mime = mime
        self._log_visit(gi, address, size)
        if update_hist:
            self._update_history(gi)

    def _send_request(self, gi):
        """Send a selector to a given host and port.
        Returns the resolved address and binary file with the reply."""
        if gi.scheme == "gemini":
            # For Gemini requests, connect to the host and port specified in the URL
            host, port = gi.host, gi.port
        elif gi.scheme == "gopher":
            # For Gopher requests, use the configured proxy
            host, port = self.options["gopher_proxy"].rsplit(":", 1)
            self._debug("Using gopher proxy: " + self.options["gopher_proxy"])
        addresses = self._get_addresses(host, port)
        # Connect to remote host by any address possible
        err = None
        for address in addresses:
            self._debug("Connecting to: " + str(address[4]))
            s = socket.socket(address[0], address[1])
            s.settimeout(self.options["timeout"])
            s = context.wrap_socket(s, server_hostname = gi.host)
            try:
                s.connect(address[4])
                break
            except OSError as e:
                err = e
        else:
            # If we couldn't connect to *any* of the addresses, just
            # bubble up the exception from the last attempt and deny
            # knowledge of earlier failures.
            raise err

        self._debug("Established {} connection.".format(s.version()))
        self._debug("Cipher is: {}.".format(s.cipher()))

        # Send request and wrap response in a file descriptor
        self._debug("Sending %s<CRLF>" % gi.url)
        s.sendall((gi.url + CRLF).encode("UTF-8"))
        return address, s.makefile(mode = "rb")

    def _get_addresses(self, host, port):
        # DNS lookup - will get IPv4 and IPv6 records if IPv6 is enabled
        if ":" in host:
            # This is likely a literal IPv6 address, so we can *only* ask for
            # IPv6 addresses or getaddrinfo will complain
            family_mask = socket.AF_INET6
        elif socket.has_ipv6 and self.options["ipv6"]:
            # Accept either IPv4 or IPv6 addresses
            family_mask = 0
        else:
            # IPv4 only
            family_mask = socket.AF_INET
        addresses = socket.getaddrinfo(host, port, family=family_mask,
                type=socket.SOCK_STREAM)
        # Sort addresses so IPv6 ones come first
        addresses.sort(key=lambda add: add[0] == socket.AF_INET6, reverse=True)

        return addresses

    def _get_handler_cmd(self, mimetype):
        # Now look for a handler for this mimetype
        # Consider exact matches before wildcard matches
        exact_matches = []
        wildcard_matches = []
        for handled_mime, cmd_str in _MIME_HANDLERS.items():
            if "*" in handled_mime:
                wildcard_matches.append((handled_mime, cmd_str))
            else:
                exact_matches.append((handled_mime, cmd_str))
        for handled_mime, cmd_str in exact_matches + wildcard_matches:
            if fnmatch.fnmatch(mimetype, handled_mime):
                break
        else:
            # Use "xdg-open" as a last resort.
            cmd_str = "xdg-open %s"
        self._debug("Using handler: %s" % cmd_str)
        return cmd_str

    def _handle_index(self, body, menu_gi, display=True):
        self.index = []
        preformatted = False
        if self.idx_filename:
            os.unlink(self.idx_filename)
        tmpf = tempfile.NamedTemporaryFile("w", encoding="UTF-8", delete=False)
        self.idx_filename = tmpf.name
        for line in body.splitlines():
            if line.startswith("```"):
                preformatted = not preformatted
            elif preformatted:
                tmpf.write(line + "\n")
            elif line.startswith("=>"):
                try:
                    gi = GeminiItem.from_map_line(line, menu_gi)
                    self.index.append(gi)
                    tmpf.write(self._format_geminiitem(len(self.index), gi) + "\n")
                except:
                    self._debug("Skipping possible link: %s" % line)
            elif line.startswith("*"):
                line = line[1:].lstrip()
                tmpf.write(textwrap.fill(line, self.options["width"],
                    initial_indent = "• ", subsequent_indent="  ") + "\n")
            elif line.startswith("###"):
                line = line[3:].lstrip()
                tmpf.write("\x1b[4m" + line + "\x1b[0m""\n")
            elif line.startswith("##"):
                line = line[2:].lstrip()
                tmpf.write("\x1b[1m" + line + "\x1b[0m""\n")
            elif line.startswith("#"):
                line = line[1:].lstrip()
                tmpf.write("\x1b[1m\x1b[4m" + line + "\x1b[0m""\n")
            else:
                tmpf.write(textwrap.fill(line, self.options["width"]) + "\n")
        tmpf.close()

        self.lookup = self.index
        self.page_index = 0
        self.index_index = -1

        if display:
            cmd_str = _MIME_HANDLERS["text/plain"]
            subprocess.call(shlex.split(cmd_str % self.idx_filename))

    def _format_geminiitem(self, index, gi, url=False):
        line = "[%d] %s" % (index, gi.name or gi.url)
        if gi.name and url:
            line += " (%s)" % gi.url
        return line

    def _show_lookup(self, offset=0, end=None, url=False):
        for n, gi in enumerate(self.lookup[offset:end]):
            print(self._format_geminiitem(n+offset+1, gi, url))

    def _update_history(self, gi):
        # Don't duplicate
        if self.history and self.history[self.hist_index] == gi:
            return
        self.history = self.history[0:self.hist_index+1]
        self.history.append(gi)
        self.hist_index = len(self.history) - 1

    def _log_visit(self, gi, address, size):
        if not address:
            return
        self.log["requests"] += 1
        self.log["bytes_recvd"] += size
        self.visited_hosts.add(address)
        if address[0] == socket.AF_INET:
            self.log["ipv4_requests"] += 1
            self.log["ipv4_bytes_recvd"] += size
        elif address[0] == socket.AF_INET6:
            self.log["ipv6_requests"] += 1
            self.log["ipv6_bytes_recvd"] += size

    def _get_active_tmpfile(self):
        if self.mime == "text/gemini":
            return self.idx_filename
        else:
            return self.tmp_filename

    def _debug(self, debug_text):
        if not self.options["debug"]:
            return
        debug_text = "\x1b[0;32m[DEBUG] " + debug_text + "\x1b[0m"
        print(debug_text)

    # Cmd implementation follows

    def default(self, line):
        if line.strip() == "EOF":
            return self.onecmd("quit")
        elif line.strip() == "..":
            return self.do_up()
        elif line.startswith("/"):
            return self.do_search(line[1:])

        # Expand abbreviated commands
        first_word = line.split()[0].strip()
        if first_word in _ABBREVS:
            full_cmd = _ABBREVS[first_word]
            expanded = line.replace(first_word, full_cmd, 1)
            return self.onecmd(expanded)

        # Try to parse numerical index for lookup table
        try:
            n = int(line.strip())
        except ValueError:
            print("What?")
            return

        try:
            gi = self.lookup[n-1]
        except IndexError:
            print ("Index too high!")
            return

        self.index_index = n
        self._go_to_gi(gi)

    ### Settings
    def do_set(self, line):
        """View or set various options."""
        if not line.strip():
            # Show all current settings
            for option in sorted(self.options.keys()):
                print("%s   %s" % (option, self.options[option]))
        elif len(line.split()) == 1:
            # Show current value of one specific setting
            option = line.strip()
            if option in self.options:
                print("%s   %s" % (option, self.options[option]))
            else:
                print("Unrecognised option %s" % option)
        else:
            # Set value of one specific setting
            option, value = line.split(" ", 1)
            if option not in self.options:
                print("Unrecognised option %s" % option)
                return
            # Validate / convert values
            if option == "gopher_proxy":
                if ":" not in value:
                    value += ":1965"
                else:
                    host, port = value.rsplit(":",1)
                    if not port.isnumeric():
                        print("Invalid proxy port %s" % port)
                        return
            elif value.isnumeric():
                value = int(value)
            elif value.lower() == "false":
                value = False
            elif value.lower() == "true":
                value = True
            else:
                try:
                    value = float(value)
                except ValueError:
                    pass
            self.options[option] = value

    def do_handler(self, line):
        """View or set handler commands for different MIME types."""
        if not line.strip():
            # Show all current handlers
            for mime in sorted(_MIME_HANDLERS.keys()):
                print("%s   %s" % (mime, _MIME_HANDLERS[mime]))
        elif len(line.split()) == 1:
            mime = line.strip()
            if mime in _MIME_HANDLERS:
                print("%s   %s" % (mime, _MIME_HANDLERS[mime]))
            else:
                print("No handler set for MIME type %s" % mime)
        else:
            mime, handler = line.split(" ", 1)
            _MIME_HANDLERS[mime] = handler
            if "%s" not in handler:
                print("Are you sure you don't want to pass the filename to the handler?")

    ### Stuff for getting around
    def do_go(self, line):
        """Go to a gemini URL or marked item."""
        line = line.strip()
        if not line:
            print("Go where?")
        # First, check for possible marks
        elif line in self.marks:
            gi = self.marks[line]
            self._go_to_gi(gi)
        # or a local file
        elif os.path.exists(os.path.expanduser(line)):
            gi = GeminiItem(None, None, os.path.expanduser(line),
                            "1", line, False)
            self._go_to_gi(gi)
        # If this isn't a mark, treat it as a URL
        else:
            self._go_to_gi(GeminiItem(line))

    @needs_gi
    def do_reload(self, *args):
        """Reload the current URL."""
        self._go_to_gi(self.gi)

    @needs_gi
    def do_up(self, *args):
        """Go up one directory in the path."""
        self._go_to_gi(self.gi.up())

    def do_back(self, *args):
        """Go back to the previous gemini item."""
        if not self.history or self.hist_index == 0:
            return
        self.hist_index -= 1
        gi = self.history[self.hist_index]
        self._go_to_gi(gi, update_hist=False)

    def do_forward(self, *args):
        """Go forward to the next gemini item."""
        if not self.history or self.hist_index == len(self.history) - 1:
            return
        self.hist_index += 1
        gi = self.history[self.hist_index]
        self._go_to_gi(gi, update_hist=False)

    def do_next(self, *args):
        """Go to next item after current in index."""
        return self.onecmd(str(self.index_index+1))

    def do_previous(self, *args):
        """Go to previous item before current in index."""
        self.lookup = self.index
        return self.onecmd(str(self.index_index-1))

    @needs_gi
    def do_root(self, *args):
        """Go to root selector of the server hosting current item."""
        self._go_to_gi(self.gi.root())

    def do_tour(self, line):
        """Add index items as waypoints on a tour, which is basically a FIFO
queue of gemini items.

Items can be added with `tour 1 2 3 4` or ranges like `tour 1-4`.
All items in current menu can be added with `tour *`.
Current tour can be listed with `tour ls` and scrubbed with `tour clear`."""
        line = line.strip()
        if not line:
            # Fly to next waypoint on tour
            if not self.waypoints:
                print("End of tour.")
            else:
                gi = self.waypoints.pop(0)
                self._go_to_gi(gi)
        elif line == "ls":
            old_lookup = self.lookup
            self.lookup = self.waypoints
            self._show_lookup()
            self.lookup = old_lookup
        elif line == "clear":
            self.waypoints = []
        elif line == "*":
            self.waypoints.extend(self.lookup)
        elif looks_like_url(line):
            self.waypoints.append(GeminiItem(line))
        else:
            for index in line.split():
                try:
                    pair = index.split('-')
                    if len(pair) == 1:
                        # Just a single index
                        n = int(index)
                        gi = self.lookup[n-1]
                        self.waypoints.append(gi)
                    elif len(pair) == 2:
                        # Two endpoints for a range of indices
                        for n in range(int(pair[0]), int(pair[1]) + 1):
                            gi = self.lookup[n-1]
                            self.waypoints.append(gi)
                    else:
                        # Syntax error
                        print("Invalid use of range syntax %s, skipping" % index)
                except ValueError:
                    print("Non-numeric index %s, skipping." % index)
                except IndexError:
                    print("Invalid index %d, skipping." % n)

    @needs_gi
    def do_mark(self, line):
        """Mark the current item with a single letter.  This letter can then
be passed to the 'go' command to return to the current item later.
Think of it like marks in vi: 'mark a'='ma' and 'go a'=''a'."""
        line = line.strip()
        if not line:
            for mark, gi in self.marks.items():
                print("[%s] %s (%s)" % (mark, gi.name, gi.url))
        elif line.isalpha() and len(line) == 1:
            self.marks[line] = self.gi
        else:
            print("Invalid mark, must be one letter")

    ### Stuff that modifies the lookup table
    def do_ls(self, line):
        """List contents of current index.
Use 'ls -l' to see URLs."""
        self.lookup = self.index
        self._show_lookup(url = "-l" in line)
        self.page_index = 0

    def do_history(self, *args):
        """Display history."""
        self.lookup = self.history
        self._show_lookup(url=True)
        self.page_index = 0

    def do_search(self, searchterm):
        """Search index (case insensitive)."""
        results = [
            gi for gi in self.lookup if searchterm.lower() in gi.name.lower()]
        if results:
            self.lookup = results
            self._show_lookup()
            self.page_index = 0
        else:
            print("No results found.")

    def emptyline(self):
        """Page through index ten lines at a time."""
        i = self.page_index
        if i > len(self.lookup):
            return
        self._show_lookup(offset=i, end=i+10)
        self.page_index += 10

    ### Stuff that does something to most recently viewed item
    @needs_gi
    def do_cat(self, *args):
        """Run most recently visited item through "cat" command."""
        subprocess.call(shlex.split("cat %s" % self._get_active_tmpfile()))

    @needs_gi
    def do_less(self, *args):
        """Run most recently visited item through "less" command."""
        cmd_str = self._get_handler_cmd(self.mime)
        cmd_str = cmd_str % self._get_active_tmpfile()
        subprocess.call("%s | less -R" % cmd_str, shell=True)

    @needs_gi
    def do_fold(self, *args):
        """Run most recently visited item through "fold" command."""
        cmd_str = self._get_handler_cmd(self.mime)
        cmd_str = cmd_str % self._get_active_tmpfile()
        subprocess.call("%s | fold -w 70 -s" % cmd_str, shell=True)

    @needs_gi
    def do_shell(self, line):
        """'cat' most recently visited item through a shell pipeline."""
        subprocess.call(("cat %s |" % self._get_active_tmpfile()) + line, shell=True)

    @needs_gi
    def do_save(self, line):
        """Save an item to the filesystem.
'save n filename' saves menu item n to the specified filename.
'save filename' saves the last viewed item to the specified filename.
'save n' saves menu item n to an automagic filename."""
        args = line.strip().split()

        # First things first, figure out what our arguments are
        if len(args) == 0:
            # No arguments given at all
            # Save current item, if there is one, to a file whose name is
            # inferred from the gemini path
            if not self.tmp_filename:
                print("You need to visit an item first!")
                return
            else:
                index = None
                filename = None
        elif len(args) == 1:
            # One argument given
            # If it's numeric, treat it as an index, and infer the filename
            try:
                index = int(args[0])
                filename = None
            # If it's not numeric, treat it as a filename and
            # save the current item
            except ValueError:
                index = None
                filename = os.path.expanduser(args[0])
        elif len(args) == 2:
            # Two arguments given
            # Treat first as an index and second as filename
            index, filename = args
            try:
                index = int(index)
            except ValueError:
                print("First argument is not a valid item index!")
                return
            filename = os.path.expanduser(filename)
        else:
            print("You must provide an index, a filename, or both.")
            return

        # Next, fetch the item to save, if it's not the current one.
        if index:
            last_gi = self.gi
            try:
                gi = self.lookup[index-1]
                self._go_to_gi(gi, update_hist = False, handle = False)
            except IndexError:
                print ("Index too high!")
                self.gi = last_gi
                return
        else:
            gi = self.gi

        # Derive filename from current GI's path, if one hasn't been set
        if not filename:
            filename = os.path.basename(gi.path)

        # Check for filename collisions and actually do the save if safe
        if os.path.exists(filename):
            print("File %s already exists!" % filename)
        else:
            # Don't use _get_active_tmpfile() here, because we want to save the
            # "source code" of menus, not the rendered view - this way AV-98
            # can navigate to it later.
            shutil.copyfile(self.tmp_filename, filename)
            print("Saved to %s" % filename)

        # Restore gi if necessary
        if index != None:
            self._go_to_gi(last_gi, handle=False)

    @needs_gi
    def do_url(self, *args):
        """Print URL of most recently visited item."""
        print(self.gi.url)

    ### Bookmarking stuff
    @needs_gi
    def do_add(self, line):
        """Add the current URL to the bookmarks menu.
Bookmarks are stored in the ~/.av98-bookmarks.txt file.
Optionally, specify the new name for the bookmark."""
        with open(os.path.expanduser("~/.av98-bookmarks.txt"), "a") as fp:
            fp.write(self.gi.to_map_line(line))

    def do_bookmarks(self, line):
        """Show or access the bookmarks menu.
'bookmarks' shows all bookmarks.
'bookmarks n' navigates immediately to item n in the bookmark menu.
Bookmarks are stored in the ~/.av98-bookmarks.txt file using the 'add' command."""
        bm_file = os.path.expanduser("~/.av98-bookmarks.txt")
        if not os.path.exists(bm_file):
            print("You need to 'add' some bookmarks, first!")
            return
        args = line.strip()
        if len(args.split()) > 1 or (args and not args.isnumeric()):
            print("bookmarks command takes a single integer argument!")
            return
        with open(bm_file, "r") as fp:
            body = fp.read()
            gi = GeminiItem("localhost/" + bm_file)
            self._handle_index(body, gi, display = not args)
            if args:
                # Use argument as a numeric index
                self.default(line)

    ### Help
    def do_help(self, arg):
        """ALARM! Recursion detected! ALARM! Prepare to eject!"""
        if arg == "!":
            print("! is an alias for 'shell'")
        elif arg == "?":
            print("? is an alias for 'help'")
        else:
            cmd.Cmd.do_help(self, arg)

    ### Flight recorder
    def do_blackbox(self, *args):
        """Display contents of flight recorder, showing statistics for the
current gemini browsing session."""
        lines = []
        # Compute flight time
        now = time.time()
        delta = now - self.log["start_time"]
        hours, remainder = divmod(delta, 36060)
        minutes, seconds = divmod(remainder, 60)
        # Count hosts
        ipv4_hosts = len([host for host in self.visited_hosts if host[0] == socket.AF_INET])
        ipv6_hosts = len([host for host in self.visited_hosts if host[0] == socket.AF_INET6])
        # Assemble lines
        lines.append(("Flight duration", "%02d:%02d:%02d" % (hours, minutes, seconds)))
        lines.append(("Requests sent:", self.log["requests"]))
        lines.append(("   IPv4 requests:", self.log["ipv4_requests"]))
        lines.append(("   IPv6 requests:", self.log["ipv6_requests"]))
        lines.append(("Bytes received:", self.log["bytes_recvd"]))
        lines.append(("   IPv4 bytes:", self.log["ipv4_bytes_recvd"]))
        lines.append(("   IPv6 bytes:", self.log["ipv6_bytes_recvd"]))
        lines.append(("Unique hosts visited:", len(self.visited_hosts)))
        lines.append(("   IPv4 hosts:", ipv4_hosts))
        lines.append(("   IPv6 hosts:", ipv6_hosts))
        lines.append(("DNS failures:", self.log["dns_failures"]))
        lines.append(("Timeouts:", self.log["timeouts"]))
        lines.append(("Refused connections:", self.log["refused_connections"]))
        lines.append(("Reset connections:", self.log["reset_connections"]))
        # Print
        for key, value in lines:
            print(key.ljust(24) + str(value).rjust(8))

    ### The end!
    def do_quit(self, *args):
        """Exit AV-98."""
        # Clean up after ourself
        if self.tmp_filename:
            os.unlink(self.tmp_filename)
        if self.idx_filename:
            os.unlink(self.idx_filename)
        print()
        print("Thank you for flying AV-98!")
        sys.exit()

    do_exit = do_quit

# Config file finder
def get_rcfile():
    rc_paths = ("~/.config/av98/av98rc", "~/.config/.av98rc", "~/.av98rc")
    for rc_path in rc_paths:
        rcfile = os.path.expanduser(rc_path)
        if os.path.exists(rcfile):
            return rcfile
    return None

# Main function
def main():

    # Parse args
    parser = argparse.ArgumentParser(description='A command line gemini client.')
    parser.add_argument('--bookmarks', action='store_true',
                        help='start with your list of bookmarks')
    parser.add_argument('--tls-cert', metavar='FILE', help='TLS client certificate file')
    parser.add_argument('--tls-key', metavar='FILE', help='TLS client certificate private key file')
    parser.add_argument('url', metavar='URL', nargs='*',
                        help='start with this URL')
    args = parser.parse_args()

    # Instantiate client
    gc = GeminiClient()

    # Process config file
    rcfile = get_rcfile()
    if rcfile:
        print("Using config %s" % rcfile)
        with open(rcfile, "r") as fp:
            for line in fp:
                line = line.strip()
                if ((args.bookmarks or args.url) and
                    any((line.startswith(x) for x in ("go", "g", "tour", "t")))
                   ):
                    if args.bookmarks:
                        print("Skipping rc command \"%s\" due to --bookmarks option." % line)
                    else:
                        print("Skipping rc command \"%s\" due to provided URLs." % line)
                    continue
                gc.cmdqueue.append(line)

    # Say hi
    print("Welcome to AV-98!")
    print("Enjoy your patrol through Geminispace...")

    # Act on args
    if args.tls_cert:
        # If tls_key is None, python will attempt to load the key from tls_cert.
        context.load_cert_chain(args.tls_cert, args.tls_key)
    if args.bookmarks:
        gc.cmdqueue.append("bookmarks")
    elif args.url:
        if len(args.url) == 1:
            gc.cmdqueue.append("go %s" % args.url[0])
        else:
            for url in args.url:
                if not url.startswith("gemini://"):
                    url = "gemini://" + url
                gc.cmdqueue.append("tour %s" % url)
            gc.cmdqueue.append("tour")

    # Endless interpret loop
    while True:
        try:
            gc.cmdloop()
        except KeyboardInterrupt:
            print("")

if __name__ == '__main__':
    main()
