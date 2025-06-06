# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members
from __future__ import annotations

import os
import platform
import shutil
import socket
from typing import TYPE_CHECKING
from typing import cast

from twisted.cred import checkers
from twisted.cred import portal
from twisted.internet import defer
from twisted.internet import reactor
from twisted.spread import pb
from twisted.trial import unittest
from zope.interface import implementer

from buildbot_worker import bot

try:
    from unittest.mock import Mock
except ImportError:
    from unittest.mock import Mock

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable

    from twisted.internet.interfaces import IListeningPort
    from twisted.internet.interfaces import IReactorTCP
    from twisted.internet.interfaces import ITransport
    from zope.interface import Interface

    from buildbot_worker.util.twisted import InlineCallbacksType

# I don't see any simple way to test the PB equipment without actually setting
# up a TCP connection.  This just tests that the PB code will connect and can
# execute a basic ping.  The rest is done without TCP (or PB) in other
# test modules.


class MasterPerspective(pb.Avatar):
    def __init__(self, on_keepalive: Callable[[], None] | None = None) -> None:
        self.on_keepalive = on_keepalive
        self.mind: object | None = None

    def perspective_keepalive(self) -> None:
        if self.on_keepalive:
            on_keepalive = self.on_keepalive
            self.on_keepalive = None
            on_keepalive()


@implementer(portal.IRealm)
class MasterRealm:
    def __init__(
        self,
        perspective: MasterPerspective,
        on_attachment: Callable[[object | None], None | defer.Deferred[None]] | None,
    ) -> None:
        self.perspective = perspective
        self.on_attachment = on_attachment

    @defer.inlineCallbacks
    def requestAvatar(
        self,
        avatarId: bytes | tuple,
        mind: object | None,
        *interfaces: type[Interface],
    ) -> InlineCallbacksType[tuple[type[Interface], object, Callable[[], None]]]:
        assert pb.IPerspective in interfaces
        self.mind = mind
        self.perspective.mind = mind
        if self.on_attachment:
            yield self.on_attachment(mind)

        return pb.IPerspective, self.perspective, lambda: None

    def shutdown(self) -> defer.Deferred[None]:
        mind = cast("pb.RemoteReference", self.mind)
        broker = cast("pb.Broker", mind.broker)
        transport = cast("ITransport", broker.transport)
        return defer.maybeDeferred(transport.loseConnection)


class TestWorker(unittest.TestCase):
    def setUp(self) -> None:
        self.realm: MasterRealm | None = None
        self.worker: bot.Worker | None = None
        self.listeningport: IListeningPort | None = None

        self.basedir = os.path.abspath("basedir")
        if os.path.exists(self.basedir):
            shutil.rmtree(self.basedir)
        os.makedirs(self.basedir)

    @defer.inlineCallbacks
    def tearDown(self) -> InlineCallbacksType[None]:  # type: ignore[override]
        if self.realm:
            yield self.realm.shutdown()
        if self.listeningport:
            yield self.listeningport.stopListening()
        if os.path.exists(self.basedir):
            shutil.rmtree(self.basedir)

    def start_master(
        self,
        perspective: MasterPerspective,
        on_attachment: Callable[[object | None], None | defer.Deferred[None]] | None = None,
    ) -> int:
        self.realm = MasterRealm(perspective, on_attachment)
        p = portal.Portal(self.realm)
        p.registerChecker(checkers.InMemoryUsernamePasswordDatabaseDontUse(testy=b"westy"))
        self.listeningport = cast("IReactorTCP", reactor).listenTCP(
            0,
            pb.PBServerFactory(p),
            interface='127.0.0.1',
        )
        # return the dynamically allocated port number
        host = self.listeningport.getHost()
        assert hasattr(host, 'port')
        return host.port

    def test_constructor_minimal(self) -> None:
        # only required arguments
        bot.Worker('mstr', 9010, 'me', 'pwd', '/s', 10, protocol='pb')

    def test_constructor_083_tac(self) -> None:
        """invocation as made from default 0.8.3 tac files"""
        bot.Worker('mstr', 9010, 'me', 'pwd', '/s', 10, umask=0o123, protocol='pb', maxdelay=10)

    def test_constructor_091_tac(self) -> None:
        # invocation as made from default 0.9.1 tac files
        bot.Worker(
            None,
            None,
            'me',
            'pwd',
            '/s',
            10,
            connection_string="tcp:host=localhost:port=9010",
            umask=0o123,
            protocol='pb',
            maxdelay=10,
        )

    def test_constructor_invalid_both_styles(self) -> None:
        """Can't instantiate with both host/port and connection string."""
        # assertRaises as a context manager appears in Python 2.7
        self.assertRaises(
            AssertionError,
            bot.Worker,
            'mstr',
            9010,
            'me',
            'pwd',
            '/s',
            10,
            connection_string="tcp:anything",
        )

    def test_constructor_invalid_both_styles_partial(self) -> None:
        # assertRaises as a context manager appears in Python 2.7
        self.assertRaises(
            AssertionError,
            bot.Worker,
            'mstr',
            None,
            'me',
            'pwd',
            '/s',
            10,
            connection_string="tcp:anything",
        )

    def test_constructor_invalid_both_styles_partial2(self) -> None:
        """Can't instantiate with both host/port and connection string."""
        # assertRaises as a context manager appears in Python 2.7
        self.assertRaises(
            AssertionError,
            bot.Worker,
            None,
            9010,
            None,
            'me',
            'pwd',
            '/s',
            10,
            connection_string="tcp:anything",
        )

    def test_constructor_full(self) -> None:
        # invocation with all args
        bot.Worker(
            'mstr',
            9010,
            'me',
            'pwd',
            '/s',
            10,
            umask=0o123,
            maxdelay=10,
            keepaliveTimeout=10,
            unicode_encoding='utf8',
            protocol='pb',
            allow_shutdown=True,  # type: ignore[arg-type]
        )

    def test_worker_print(self) -> defer.Deferred[None]:
        d: defer.Deferred[None] = defer.Deferred()

        # set up to call print when we are attached, and chain the results onto
        # the deferred for the whole test
        def call_print(mind: object | None) -> None:
            print_d = cast("pb.RemoteReference", mind).callRemote("print", "Hi, worker.")
            assert isinstance(print_d, defer.Deferred)
            print_d.addCallbacks(d.callback, d.errback)

        # start up the master and worker
        persp = MasterPerspective()
        port = self.start_master(persp, on_attachment=call_print)
        self.worker = bot.Worker(
            "127.0.0.1",
            port,
            "testy",
            "westy",
            self.basedir,
            keepalive=0,
            umask=0o22,
            protocol='pb',
        )
        self.worker.startService()
        self.addCleanup(self.worker.stopService)

        # and wait for the result of the print
        return d

    def test_recordHostname_uname(self) -> None:
        def _uname() -> os.uname_result:
            return os.uname_result(('0', 'test-hostname.domain.com', '', '', ''))

        self.patch(platform, 'uname', _uname)

        self.worker = bot.Worker(
            "127.0.0.1",
            9999,
            "testy",
            "westy",
            self.basedir,
            keepalive=0,
            umask=0o22,
            protocol='pb',
        )
        self.worker.recordHostname(self.basedir)
        with open(os.path.join(self.basedir, "twistd.hostname")) as f:
            twistdHostname = f.read().strip()
        self.assertEqual(twistdHostname, 'test-hostname.domain.com')

    def test_recordHostname_getfqdn(self) -> None:
        def _uname() -> os.uname_result:
            return os.uname_result(('', '', '', '', ''))

        self.patch(platform, 'uname', _uname)
        self.patch(socket, "getfqdn", lambda: 'test-hostname.domain.com')

        self.worker = bot.Worker(
            "127.0.0.1",
            9999,
            "testy",
            "westy",
            self.basedir,
            keepalive=0,
            umask=0o22,
            protocol='pb',
        )
        self.worker.recordHostname(self.basedir)
        with open(os.path.join(self.basedir, "twistd.hostname")) as f:
            twistdHostname = f.read().strip()
        self.assertEqual(twistdHostname, 'test-hostname.domain.com')

    def test_worker_graceful_shutdown(self) -> defer.Deferred[None]:
        """Test that running the build worker's gracefulShutdown method results
        in a call to the master's shutdown method"""
        d: defer.Deferred[None] = defer.Deferred()

        fakepersp = Mock()
        called: list[tuple[str, ...]] = []

        def fakeCallRemote(*args: str) -> defer.Deferred[None]:
            called.append(args)
            d1 = defer.succeed(None)
            return d1

        fakepersp.callRemote = fakeCallRemote

        # set up to call shutdown when we are attached, and chain the results onto
        # the deferred for the whole test
        def call_shutdown(mind: object | None) -> None:
            assert self.worker is not None
            self.worker.bf.perspective = fakepersp
            shutdown_d = self.worker.gracefulShutdown()
            assert shutdown_d is not None
            shutdown_d.addCallbacks(d.callback, d.errback)

        persp = MasterPerspective()
        port = self.start_master(persp, on_attachment=call_shutdown)

        self.worker = bot.Worker(
            "127.0.0.1",
            port,
            "testy",
            "westy",
            self.basedir,
            keepalive=0,
            umask=0o22,
            protocol='pb',
        )

        self.worker.startService()
        self.addCleanup(self.worker.stopService)

        def check(ign: Any) -> None:
            self.assertEqual(called, [('shutdown',)])

        d.addCallback(check)

        return d

    def test_worker_shutdown(self) -> None:
        """Test watching an existing shutdown_file results in gracefulShutdown
        being called."""

        worker = bot.Worker(
            "127.0.0.1",
            1234,
            "testy",
            "westy",
            self.basedir,
            keepalive=0,
            umask=0o22,
            protocol='pb',
            allow_shutdown='file',
        )

        # Mock out gracefulShutdown
        worker.gracefulShutdown = Mock()  # type: ignore[method-assign]

        # Mock out os.path methods
        exists = Mock()
        mtime = Mock()

        self.patch(os.path, 'exists', exists)
        self.patch(os.path, 'getmtime', mtime)

        # Pretend that the shutdown file doesn't exist
        mtime.return_value = 0
        exists.return_value = False

        worker._checkShutdownFile()

        # We shouldn't have called gracefulShutdown
        self.assertEqual(worker.gracefulShutdown.call_count, 0)

        # Pretend that the file exists now, with an mtime of 2
        exists.return_value = True
        mtime.return_value = 2
        worker._checkShutdownFile()

        # Now we should have changed gracefulShutdown
        self.assertEqual(worker.gracefulShutdown.call_count, 1)

        # Bump the mtime again, and make sure we call shutdown again
        mtime.return_value = 3
        worker._checkShutdownFile()
        self.assertEqual(worker.gracefulShutdown.call_count, 2)

        # Try again, we shouldn't call shutdown another time
        worker._checkShutdownFile()
        self.assertEqual(worker.gracefulShutdown.call_count, 2)
