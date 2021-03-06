"""FogHorn - DNS Graylisting"""

import logging
from datetime import datetime
import dateutil.parser

from twisted.internet import defer
from twisted.names import dns, error

from greylist_entry import GreylistEntry


class Foghorn(object):
    """Manage lists of greylist entries and handles the list checks."""

    def __init__(self, settings):
        self.settings = settings
        self._peer_address = None
        self.logging = logging.getLogger('foghornd')
        self.whitelist = set(load_list(self.settings.whitelist_file))
        self.blacklist = set(load_list(self.settings.blacklist_file))
        self.greylist = {}
        for item in load_list(self.settings.greylist_file):
            elements = [n.strip() for n in item.split(',')]
            entry = GreylistEntry(
                elements[0],
                dateutil.parser.parse(elements[1]),
                dateutil.parser.parse(elements[2])
            )
            self.greylist[elements[0]] = entry

    def save_state(self):
        """Called as the program is shutting down, put shut down tasks here."""
        write_list(self.settings.greylist_file, self.greylist)

    @property
    def peer_address(self):
        """peer_address is injected in here for logging"""
        return self._peer_address

    @peer_address.setter
    def peer_address(self, value):
        self._peer_address = value

    def list_check(self, query):
        """
        Handle rules regarding what resolves by checking whether
        the record requested is in our lists. Order is important.
        """
        if query.type == dns.A:
            key = query.name.name
            if key in self.whitelist:
                self.logging.debug('Allowed by whitelist %s ref-by %s', key, self.peer_address)
                return True
            if key in self.blacklist:
                # Key is in blacklist
                self.logging.debug('Rejected by blacklist %s ref-by %s', key, self.peer_address)
                return False
            if self.greylist.has_key(key):
                # Key exists in greylist
                curtime = datetime.now()
                entry = self.greylist[key]
                if (curtime - self.settings.grey_out) >= entry.firstSeen:
                    # Is the entry in the greyout period?
                    if curtime - self.settings.blackout <= entry.lastSeen:
                        # Is the entry in the blackout period?
                        self.logging.debug('Allowed by greylist %s ref-by %s',
                                           key, self.peer_address)
                        return True
                    else:
                        self.logging.debug('Rejected/timeout by greylist %s ref-by %s',
                                           key, self.peer_address)
                        entry.firstSeen()
                        entry.lastSeen()
                        return False
                else:
                    self.logging.debug('Rejected/greyout by greylist %s ref-by %s',
                                       key, self.peer_address)
                    return False
        else:
            # Entry not found in any list, so add it
            self.logging.debug('Rejected/notseen by greylist %s ref-by %s',
                               key, self.peer_address)
            entry = GreylistEntry(key)
            self.greylist[key] = entry
            return False
    def build_response(self, query):
        """Build sinkholed response when disallowing a response."""
        name = query.name.name
        answer = dns.RRHeader(name=name,
                              payload=dns.Record_A(address=b'%s' % (self.settings.sinkhole)))
        answers = [answer]
        authority = []
        additional = []
        return answers, authority, additional

    def query(self, query, timeout=0):
        """
        Either return our fake response, or let it on through to the next resolver
        in the chain
        """
        # Disable the warning that timeout is unused. We have to
        # accept the argument.
        # pylint: disable=W0613
        if not self.list_check(query):
            return defer.succeed(self.build_response(query))
        else:
            return defer.fail(error.DomainError())


def write_list(filename, items):
    """Write out [gray|whit|black] blists"""
    greylist_entries = False
    if len(items.keys()) > 0 and isinstance(items.itervalues().next(), GreylistEntry):
        greylist_entries = True
    else:
        # We're not going to support writing the other lists at the moment
        return False

    try:
        with open(filename, mode='w') as write_file:
            if greylist_entries:
                for item in items.itervalues():
                    write_file.write(format("%s\n", item))
                    return True
    except IOError as io_error:
        print "%s" % io_error
        return False

def load_list(filename):
    """Load the specified list."""
    lines = []
    try:
        with open(filename, mode='r') as read_file:
            lines = [x.strip() for x in read_file.readlines()]
            return lines
    except IOError as io_error:
        print "%s" % io_error
        return []

